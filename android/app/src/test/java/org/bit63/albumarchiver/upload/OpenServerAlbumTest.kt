package org.bit63.albumarchiver.upload

import com.google.common.truth.Truth.assertThat
import org.bit63.albumarchiver.TestEnv
import org.bit63.albumarchiver.blocking
import org.bit63.albumarchiver.data.AddShotResult
import org.bit63.albumarchiver.data.ShotState
import org.bit63.albumarchiver.testing.FakeAlbumServer
import org.junit.After
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import java.io.File

/** Opening an album that is only on the server, and loading it on demand (Requirement 14). */
@RunWith(RobolectricTestRunner::class)
class OpenServerAlbumTest {
    private val fake = FakeAlbumServer().start()
    private val env = TestEnv(fake.url, fake.token)
    private val server = ServerAccess(env.settings)
    private val repo = env.repo
    private val pageLoader = PageLoader(repo, server)
    private val shotFetcher = ShotFetcher(repo, env.store, server)
    private val lastPageLoader = LastPageLoader(repo, pageLoader, shotFetcher)
    private val thumbs = ThumbFetcher(File(env.dir, "thumbs"), server)
    private val loadRequests = mutableListOf<String>()
    private val importer = AlbumImporter(repo, server, env.store::isLowOnSpace) { loadRequests += it }
    private val processor = UploadProcessor(env.db, env.settings, server)

    @After fun tearDown() {
        fake.shutdown()
        env.close()
    }

    private fun seed() = fake.seed(
        "srv", "Grandma", "a4",
        listOf(
            listOf("a1" to FakeAlbumServer.jpeg("a1"), "a2" to FakeAlbumServer.jpeg("a2")),
            listOf("b1" to FakeAlbumServer.jpeg("b1")),
            listOf("c1" to FakeAlbumServer.jpeg("c1"), "c2" to FakeAlbumServer.jpeg("c2"), "c3" to FakeAlbumServer.jpeg("c3")),
        ),
    )

    private fun requests() = fake.log.map { it.substringBefore(" ") + " " + it.substringAfter("/api/v1/") }

    @Test fun `opening fetches only the album's metadata and writes unloaded pages`() = blocking {
        seed()
        assertThat(importer.open("srv")).isEqualTo(AlbumImporter.Result.Opened)
        assertThat(requests()).containsExactly("GET albums/srv")
        val album = repo.album("srv")!!
        assertThat(album.name).isEqualTo("Grandma")
        assertThat(album.pageSize).isEqualTo("a4")
        val pages = repo.pages("srv")
        assertThat(pages.map { it.shotCount }).containsExactly(2, 1, 3).inOrder()
        assertThat(pages.none { it.shotsLoaded }).isTrue()
        assertThat(loadRequests).containsExactly("srv")
        assertThat(env.db.ops().all()).isEmpty()
    }

    @Test fun `the last page is then loaded with its full shots, and nothing else`() = blocking {
        seed()
        importer.open("srv")
        fake.log.clear()
        assertThat(lastPageLoader.load("srv")).isTrue()
        val last = repo.lastPage("srv")!!
        assertThat(requests()).containsExactly(
            "GET albums/srv/pages/srv-page-3",
            "GET albums/srv/pages/srv-page-3/shots/c1",
            "GET albums/srv/pages/srv-page-3/shots/c2",
            "GET albums/srv/pages/srv-page-3/shots/c3",
        ).inOrder()
        val shots = repo.shots(last.id)
        assertThat(shots.map { it.id }).containsExactly("c1", "c2", "c3").inOrder()
        assertThat(shots.all { it.state == ShotState.PRESENT }).isTrue()
        assertThat(File(shots[0].path).readBytes()).isEqualTo(FakeAlbumServer.jpeg("c1"))
        assertThat(repo.pages("srv").take(2).none { it.shotsLoaded }).isTrue()
    }

    @Test fun `opening fails cleanly and creates nothing when the server is down or rejects the token`() = blocking {
        seed()
        fake.failNext += 500
        assertThat(importer.open("srv")).isEqualTo(AlbumImporter.Result.Failed(500))
        fake.token = "other"
        assertThat(importer.open("srv")).isEqualTo(AlbumImporter.Result.AuthFailed)
        env.settings.setServer("http://127.0.0.1:1", "x")
        assertThat(importer.open("srv")).isEqualTo(AlbumImporter.Result.Unreachable)
        env.settings.setServer("", "")
        assertThat(importer.open("srv")).isEqualTo(AlbumImporter.Result.NoServer)
        assertThat(repo.album("srv")).isNull()
    }

    @Test fun `opening is refused while the phone is low on space`() = blocking {
        seed()
        env.freeBytes = 100L * 1024 * 1024
        assertThat(importer.open("srv")).isEqualTo(AlbumImporter.Result.LowOnSpace)
        assertThat(fake.log).isEmpty()
        assertThat(repo.album("srv")).isNull()
        env.freeBytes = 10L * 1024 * 1024 * 1024
        assertThat(importer.open("srv")).isEqualTo(AlbumImporter.Result.Opened)
    }

    @Test fun `an album with no page size gets the default, and the server is told`() = blocking {
        seed().pageSize = null
        importer.open("srv")
        assertThat(repo.album("srv")!!.pageSize).isEqualTo("letter")
        assertThat(processor.drain()).isEqualTo(UploadProcessor.Outcome.DONE)
        val stored = fake.album("srv")!!
        assertThat(stored.pageSize).isEqualTo("letter")
        assertThat(stored.pages.map { it.id }).containsExactly("srv-page-1", "srv-page-2", "srv-page-3").inOrder()
        assertThat(stored.pages.map { it.shots.size }).containsExactly(2, 1, 3).inOrder()
    }

    @Test fun `viewing a page fetches its shot list`() = blocking {
        seed()
        importer.open("srv")
        val p1 = repo.pages("srv").first()
        assertThat(pageLoader.load("srv", p1.id)).isTrue()
        val shots = repo.shots(p1.id)
        assertThat(shots.map { it.id to it.state }).containsExactly("a1" to ShotState.NOT_DOWNLOADED, "a2" to ShotState.NOT_DOWNLOADED).inOrder()
        assertThat(shots.first().sha256).isEqualTo(FakeAlbumServer.sha256(FakeAlbumServer.jpeg("a1")))
        assertThat(repo.page(p1.id)!!.shotsLoaded).isTrue()
    }

    @Test fun `a failed page fetch leaves the page unloaded for a later retry`() = blocking {
        seed()
        importer.open("srv")
        val p1 = repo.pages("srv").first()
        fake.failNext += 503
        assertThat(pageLoader.load("srv", p1.id)).isFalse()
        assertThat(repo.page(p1.id)!!.shotsLoaded).isFalse()
        assertThat(pageLoader.load("srv", p1.id)).isTrue()
    }

    @Test fun `thumbnails use the thumb size and are cached`() = blocking {
        seed()
        importer.open("srv")
        val p1 = repo.pages("srv").first()
        pageLoader.load("srv", p1.id)
        val shot = repo.shots(p1.id).first()
        fake.log.clear()
        val f = thumbs.fetch(shot)!!
        assertThat(f.readBytes()).isEqualTo(FakeAlbumServer.thumbBytes("a1"))
        assertThat(thumbs.fetch(shot)).isEqualTo(f)
        assertThat(requests()).containsExactly("GET albums/srv/pages/srv-page-1/shots/a1?size=thumb")
        assertThat(repo.shot("a1")!!.state).isEqualTo(ShotState.NOT_DOWNLOADED)
    }

    @Test fun `review fetches the full shot, checks its hash and marks it present`() = blocking {
        seed()
        importer.open("srv")
        val p1 = repo.pages("srv").first()
        pageLoader.load("srv", p1.id)
        val shot = repo.shot("a2")!!
        assertThat(shotFetcher.fetch(shot)).isEqualTo(ShotFetcher.Result.FETCHED)
        val after = repo.shot("a2")!!
        assertThat(after.state).isEqualTo(ShotState.PRESENT)
        assertThat(ShotStoreSha(after.path)).isEqualTo(after.sha256)
    }

    @Test fun `a corrupted download is discarded and fetched again`() = blocking {
        seed()
        importer.open("srv")
        pageLoader.load("srv", repo.pages("srv").first().id)
        fake.corruptNextDownloads = 1
        assertThat(shotFetcher.fetch(repo.shot("a1")!!)).isEqualTo(ShotFetcher.Result.FETCHED)
        assertThat(fake.log.count { it.endsWith("/shots/a1") }).isEqualTo(2)
        assertThat(File(repo.shot("a1")!!.path).readBytes()).isEqualTo(FakeAlbumServer.jpeg("a1"))
    }

    @Test fun `a shot corrupted twice is reported as failed and leaves no file`() = blocking {
        seed()
        importer.open("srv")
        pageLoader.load("srv", repo.pages("srv").first().id)
        fake.corruptNextDownloads = 2
        val shot = repo.shot("a1")!!
        assertThat(shotFetcher.fetch(shot)).isEqualTo(ShotFetcher.Result.FAILED)
        assertThat(File(shot.path).exists()).isFalse()
        assertThat(repo.shot("a1")!!.state).isEqualTo(ShotState.NOT_DOWNLOADED)
        assertThat(shotFetcher.fetch(shot)).isEqualTo(ShotFetcher.Result.FETCHED)
    }

    @Test fun `a shot missing on the server is removed from the page`() = blocking {
        seed()
        importer.open("srv")
        val p1 = repo.pages("srv").first()
        pageLoader.load("srv", p1.id)
        fake.album("srv")!!.pages.first().shots.removeAll { it.id == "a1" }
        assertThat(shotFetcher.fetch(repo.shot("a1")!!)).isEqualTo(ShotFetcher.Result.MISSING)
        assertThat(repo.shot("a1")).isNull()
        assertThat(repo.page(p1.id)!!.shotCount).isEqualTo(1)
    }

    @Test fun `deleting unfetched shots and pages fetches nothing and is sent to the server`() = blocking {
        seed()
        importer.open("srv")
        val pages = repo.pages("srv")
        pageLoader.load("srv", pages[0].id)
        fake.log.clear()
        repo.deleteShot("a1")
        repo.deletePage(pages[1].id)
        assertThat(fake.log).isEmpty()
        processor.drain()
        assertThat(requests().none { it.startsWith("GET") }).isTrue()
        val stored = fake.album("srv")!!
        assertThat(stored.pages.map { it.id }).containsExactly("srv-page-1", "srv-page-3").inOrder()
        assertThat(stored.pages.first().shots.map { it.id }).containsExactly("a2")
    }

    @Test fun `shooting on an opened album uploads only the new shots`() = blocking {
        seed()
        importer.open("srv")
        lastPageLoader.load("srv")
        fake.log.clear()
        val r = repo.addShot("srv", "new", env.written("new")) as AddShotResult.Added
        assertThat(r.page.id).isEqualTo("srv-page-3")
        processor.drain()
        assertThat(requests()).containsExactly("PUT albums/srv/pages/srv-page-3/shots/new")
        assertThat(fake.album("srv")!!.pages.last().shots.map { it.id }).containsExactly("c1", "c2", "c3", "new").inOrder()
    }

    @Test fun `opening an album already on the phone is a no-op`() = blocking {
        seed()
        importer.open("srv")
        fake.log.clear()
        assertThat(importer.open("srv")).isEqualTo(AlbumImporter.Result.Opened)
        assertThat(fake.log).isEmpty()
    }

    private fun ShotStoreSha(path: String) = org.bit63.albumarchiver.data.ShotStore.sha256(File(path))
}
