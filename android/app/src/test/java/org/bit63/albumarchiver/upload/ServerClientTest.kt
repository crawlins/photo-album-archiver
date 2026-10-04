package org.bit63.albumarchiver.upload

import com.google.common.truth.Truth.assertThat
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.bit63.albumarchiver.data.ServerConfig
import org.bit63.albumarchiver.testing.FakeAlbumServer
import org.junit.After
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

class ServerClientTest {
    @get:Rule val tmp = TemporaryFolder()
    private val server = MockWebServer()
    private lateinit var client: ServerClient

    @Before fun setUp() {
        server.start()
        client = ServerClient(ServerConfig(server.url("/base/").toString(), "tok"))
    }

    @After fun tearDown() = server.shutdown()

    private fun respond(code: Int, body: String = "") = server.enqueue(MockResponse().setResponseCode(code).setBody(body))

    @Test fun `every request carries the bearer token under api v1`() = runBlocking<Unit> {
        respond(200)
        assertThat(client.ping()).isEqualTo(ApiResult.Ok(Unit, 200))
        val r = server.takeRequest()
        assertThat(r.method).isEqualTo("GET")
        assertThat(r.path).isEqualTo("/base/api/v1/ping")
        assertThat(r.getHeader("Authorization")).isEqualTo("Bearer tok")
    }

    @Test fun `401 and 403 are auth failures, other errors keep their code`() = runBlocking<Unit> {
        respond(401); respond(403); respond(500); respond(404)
        assertThat(client.ping()).isEqualTo(ApiResult.AuthFailed)
        assertThat(client.ping()).isEqualTo(ApiResult.AuthFailed)
        assertThat(client.ping()).isEqualTo(ApiResult.HttpError(500))
        assertThat(client.ping()).isEqualTo(ApiResult.HttpError(404))
    }

    @Test fun `an unreachable server is a network error`() = runBlocking<Unit> {
        val port = server.port
        server.shutdown()
        val dead = ServerClient(ServerConfig("http://127.0.0.1:$port", "tok"))
        assertThat(dead.ping()).isInstanceOf(ApiResult.NetworkError::class.java)
    }

    @Test fun `putAlbum sends the metadata as JSON`() = runBlocking<Unit> {
        respond(200)
        val meta = AlbumMetadata("Family", "8.5x11in", listOf("p1", "p2"), "2026-10-04T01:09:19Z")
        assertThat(client.putAlbum("a1", meta)).isInstanceOf(ApiResult.Ok::class.java)
        val r = server.takeRequest()
        assertThat(r.method).isEqualTo("PUT")
        assertThat(r.path).isEqualTo("/base/api/v1/albums/a1")
        assertThat(r.getHeader("Content-Type")).startsWith("application/json")
        assertThat(r.body.readUtf8()).isEqualTo(
            """{"name":"Family","page_size":"8.5x11in","pages":["p1","p2"],"created":"2026-10-04T01:09:19Z"}"""
        )
    }

    @Test fun `putShot sends the JPEG with its hash`() = runBlocking<Unit> {
        respond(201)
        val bytes = FakeAlbumServer.jpeg("x")
        val file = tmp.newFile("s.jpg").apply { writeBytes(bytes) }
        val sha = FakeAlbumServer.sha256(bytes)
        assertThat(client.putShot("a", "p", "s", file, sha)).isEqualTo(ApiResult.Ok(Unit, 201))
        val r = server.takeRequest()
        assertThat(r.method).isEqualTo("PUT")
        assertThat(r.path).isEqualTo("/base/api/v1/albums/a/pages/p/shots/s")
        assertThat(r.getHeader("X-Content-SHA256")).isEqualTo(sha)
        assertThat(r.getHeader("Content-Type")).isEqualTo("image/jpeg")
        assertThat(r.body.readByteArray()).isEqualTo(bytes)
    }

    @Test fun `deletions use the right paths`() = runBlocking<Unit> {
        repeat(3) { respond(204) }
        client.deleteShot("a", "p", "s")
        client.deletePage("a", "p")
        client.deleteAlbum("a")
        assertThat((1..3).map { server.takeRequest().let { "${it.method} ${it.path}" } }).containsExactly(
            "DELETE /base/api/v1/albums/a/pages/p/shots/s",
            "DELETE /base/api/v1/albums/a/pages/p",
            "DELETE /base/api/v1/albums/a",
        ).inOrder()
    }

    @Test fun `listAlbums parses the list, tolerating unknown keys and a missing page size`() = runBlocking<Unit> {
        respond(200, """{"albums":[{"id":"3d2a","name":"Rawlins","page_size":"8.5x11in","pages":42,"shots":131,"updated":"2026-10-04T01:27:08Z","extra":1},
            {"id":"x","name":"Untitled","page_size":null,"pages":0,"shots":0,"updated":"2026-10-04T01:27:08Z"}]}""")
        val list = (client.listAlbums() as ApiResult.Ok).value
        assertThat(list).containsExactly(
            ServerAlbumSummary("3d2a", "Rawlins", "8.5x11in", 42, 131, "2026-10-04T01:27:08Z"),
            ServerAlbumSummary("x", "Untitled", null, 0, 0, "2026-10-04T01:27:08Z"),
        ).inOrder()
    }

    @Test fun `getAlbum and getPage parse the contract's examples`() = runBlocking<Unit> {
        respond(200, """{"id":"3d2a","name":"R","page_size":"a4","created":"2026-10-04T01:09:19Z","pages":[{"id":"5f0c","shots":3},{"id":"a91e","shots":4}]}""")
        respond(200, """{"id":"5f0c","shots":[{"id":"b7e1","sha256":"9f86","bytes":4183022,"taken":"2026-10-04T01:12:40Z"}]}""")
        val album = (client.getAlbum("3d2a") as ApiResult.Ok).value
        assertThat(album.pages).containsExactly(ServerPageRef("5f0c", 3), ServerPageRef("a91e", 4)).inOrder()
        val page = (client.getPage("3d2a", "5f0c") as ApiResult.Ok).value
        assertThat(page.shots.single()).isEqualTo(ServerShot("b7e1", "9f86", 4183022, "2026-10-04T01:12:40Z"))
    }

    @Test fun `a malformed body is reported as an error, not a crash`() = runBlocking<Unit> {
        respond(200, "not json")
        assertThat(client.listAlbums()).isEqualTo(ApiResult.HttpError(200))
    }

    @Test fun `getShot streams the body with its hash, and asks for the thumb size`() = runBlocking<Unit> {
        server.enqueue(MockResponse().setBody("JPEG").addHeader("X-Content-SHA256", "abc"))
        server.enqueue(MockResponse().setBody("small"))
        (client.getShot("a", "p", "s") as ApiResult.Ok).value.use {
            assertThat(it.sha256).isEqualTo("abc")
            assertThat(it.stream.readBytes().decodeToString()).isEqualTo("JPEG")
        }
        (client.getShot("a", "p", "s", thumb = true) as ApiResult.Ok).value.use {
            assertThat(it.stream.readBytes().decodeToString()).isEqualTo("small")
        }
        assertThat(server.takeRequest().path).isEqualTo("/base/api/v1/albums/a/pages/p/shots/s")
        assertThat(server.takeRequest().path).isEqualTo("/base/api/v1/albums/a/pages/p/shots/s?size=thumb")
    }

    @Test fun `ids are path-escaped`() = runBlocking<Unit> {
        respond(204)
        client.deleteAlbum("a b/c")
        assertThat(server.takeRequest().path).isEqualTo("/base/api/v1/albums/a%20b%2Fc")
    }

    @Test fun `url checks`() {
        assertThat(ServerClient.urlProblem("")).isNull()
        assertThat(ServerClient.urlProblem("https://archive.example:8080")).isNull()
        assertThat(ServerClient.urlProblem("http://192.168.1.5:8080/")).isNull()
        assertThat(ServerClient.urlProblem("archive.example")).isNotNull()
        assertThat(ServerClient.urlProblem("ftp://archive.example")).isNotNull()
        assertThat(ServerClient.isCleartext("http://x")).isTrue()
        assertThat(ServerClient.isCleartext(" HTTP://x")).isTrue()
        assertThat(ServerClient.isCleartext("https://x")).isFalse()
    }
}
