package org.bit63.albumarchiver.testing

import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import okio.Buffer
import org.bit63.albumarchiver.upload.AlbumMetadata
import org.bit63.albumarchiver.upload.MoveRequest
import org.bit63.albumarchiver.upload.ServerAlbum
import org.bit63.albumarchiver.upload.ServerAlbumList
import org.bit63.albumarchiver.upload.ServerAlbumSummary
import org.bit63.albumarchiver.upload.ServerClient
import org.bit63.albumarchiver.upload.ServerPage
import org.bit63.albumarchiver.upload.ServerPageRef
import org.bit63.albumarchiver.upload.ServerShot
import java.security.MessageDigest
import java.time.Instant
import java.util.concurrent.CopyOnWriteArrayList

/**
 * An in-memory implementation of the album server's HTTP contract (design,
 * "Server contract"), served by [MockWebServer]. The real server does not
 * exist yet, so the app is tested against this. Failures can be injected with
 * [failNext], [corruptNextDownloads] and [token].
 */
class FakeAlbumServer : Dispatcher() {
    class StoredShot(val id: String, val bytes: ByteArray, val sha256: String, val taken: Instant)
    class StoredPage(val id: String, val shots: MutableList<StoredShot> = mutableListOf())
    class StoredAlbum(
        val id: String,
        var name: String,
        var pageSize: String?,
        var created: String,
        val pages: MutableList<StoredPage> = mutableListOf(),
        var updated: Instant = Instant.now(),
    )

    val server = MockWebServer().also { it.dispatcher = this }
    val url: String get() = server.url("/").toString().trimEnd('/')

    @Volatile var token = "secret"
    val albums = LinkedHashMap<String, StoredAlbum>()

    /** "METHOD path" of every request, in order. */
    val log: MutableList<String> = CopyOnWriteArrayList()

    /** The page ids of every album metadata PUT, in order. */
    val metadataPages: MutableList<List<String>> = CopyOnWriteArrayList()

    /** Status codes to answer the next requests with, before any handling. */
    val failNext = ArrayDeque<Int>()

    /** While set, every preview (`?size=thumb`) request is answered 503. */
    @Volatile var thumbsDown = false

    /** How many upcoming full-shot downloads get a flipped byte. */
    @Volatile var corruptNextDownloads = 0

    fun start() = apply { server.start() }
    override fun shutdown() {
        // Idle keep-alive connections would make MockWebServer wait for them.
        ServerClient.defaultHttpClient.connectionPool.evictAll()
        // Under Robolectric System.nanoTime() is a fake clock, so MockWebServer's
        // wait for its task queue never sees time pass and gives up only after
        // several seconds. Its socket is closed before that wait, which is all
        // a test needs, so the wait happens off the test thread.
        Thread {
            try {
                server.shutdown()
            } catch (e: AssertionError) {
                // See above.
            }
        }.apply { isDaemon = true }.start()
    }

    fun album(id: String) = synchronized(this) { albums[id] }

    /** Adds an album as if another phone had uploaded it. */
    fun seed(id: String, name: String, pageSize: String, pages: List<List<Pair<String, ByteArray>>>): StoredAlbum =
        synchronized(this) {
            val a = StoredAlbum(id, name, pageSize, "2026-01-02T03:04:05Z")
            pages.forEachIndexed { i, shots ->
                val p = StoredPage("$id-page-${i + 1}")
                shots.forEachIndexed { j, (shotId, bytes) ->
                    p.shots += StoredShot(shotId, bytes, sha256(bytes), Instant.parse("2026-01-02T03:04:05Z").plusSeconds(j.toLong()))
                }
                a.pages += p
            }
            albums[id] = a
            a
        }

    override fun dispatch(request: RecordedRequest): MockResponse = synchronized(this) {
        val path = request.requestUrl!!.encodedPath
        log += "${request.method} $path" + (request.requestUrl!!.query?.let { "?$it" } ?: "")
        failNext.removeFirstOrNull()?.let { return MockResponse().setResponseCode(it) }
        if (thumbsDown && request.requestUrl!!.queryParameter("size") == "thumb") return status(503)
        if (request.getHeader("Authorization") != "Bearer $token") return status(401)
        val seg = path.removePrefix("/api/v1/").split('/').filter { it.isNotEmpty() }
        when {
            seg == listOf("ping") && request.method == "GET" -> status(200)
            seg == listOf("albums") && request.method == "GET" -> json(ServerAlbumList(albums.values.sortedByDescending { it.updated }.map { it.summary() }))
            seg.size == 2 && seg[0] == "albums" -> albumRequest(request, seg[1])
            seg.size == 4 && seg[0] == "albums" && seg[2] == "pages" -> pageRequest(request, seg[1], seg[3])
            seg.size == 5 && seg[0] == "albums" && seg[2] == "pages" && seg[4] == "move" && request.method == "POST" ->
                moveRequest(request, seg[1], seg[3])
            seg.size == 6 && seg[0] == "albums" && seg[2] == "pages" && seg[4] == "shots" ->
                shotRequest(request, seg[1], seg[3], seg[5])
            else -> status(404)
        }
    }

    private fun albumRequest(request: RecordedRequest, albumId: String): MockResponse = when (request.method) {
        "GET" -> albums[albumId]?.let { a ->
            json(ServerAlbum(a.id, a.name, a.pageSize, a.created, a.pages.map { ServerPageRef(it.id, it.shots.size) }))
        } ?: status(404)
        "PUT" -> {
            val meta = ServerClient.json.decodeFromString<AlbumMetadata>(request.body.readUtf8())
            metadataPages += meta.pages
            if (meta.name.isBlank()) return status(400)
            if (meta.pages.size > MAX_PAGES) return status(422)
            val a = albums.getOrPut(albumId) { StoredAlbum(albumId, meta.name, meta.pageSize, meta.created) }
            a.name = meta.name
            a.pageSize = meta.pageSize
            a.created = meta.created
            // The listed order is the order; pages with shots that are not listed are kept (stale metadata).
            val byId = a.pages.associateBy { it.id }
            val listed = meta.pages.map { byId[it] ?: StoredPage(it) }
            val kept = a.pages.filter { it.id !in meta.pages && it.shots.isNotEmpty() }
            a.pages.clear()
            a.pages += listed + kept
            a.updated = Instant.now()
            status(200)
        }
        "DELETE" -> {
            albums.remove(albumId)
            status(204)
        }
        else -> status(405)
    }

    private fun pageRequest(request: RecordedRequest, albumId: String, pageId: String): MockResponse = when (request.method) {
        "GET" -> albums[albumId]?.pages?.firstOrNull { it.id == pageId }?.let { p ->
            json(ServerPage(p.id, p.shots.map { ServerShot(it.id, it.sha256, it.bytes.size.toLong(), it.taken.toString()) }))
        } ?: status(404)
        "DELETE" -> {
            albums[albumId]?.let { a -> a.pages.removeAll { it.id == pageId }; a.updated = Instant.now() }
            status(204)
        }
        else -> status(405)
    }

    /** Moves the listed shots of the album to the page, creating it at the end; unknown and already-moved shots are skipped. */
    private fun moveRequest(request: RecordedRequest, albumId: String, pageId: String): MockResponse {
        val ids = ServerClient.json.decodeFromString<MoveRequest>(request.body.readUtf8()).shots
        val a = albums[albumId] ?: return status(404)
        val target = a.pages.firstOrNull { it.id == pageId } ?: run {
            if (a.pages.size >= MAX_PAGES) return status(422)
            StoredPage(pageId).also { a.pages += it }
        }
        val moving = a.pages.filter { it !== target }.flatMap { p -> p.shots.filter { it.id in ids }.map { p to it } }
        if (target.shots.size + moving.size > MAX_SHOTS) return status(422)
        for ((from, shot) in moving) {
            from.shots.remove(shot)
            target.shots += shot
        }
        target.shots.sortBy { it.taken }
        a.updated = Instant.now()
        return status(204)
    }

    private fun shotRequest(request: RecordedRequest, albumId: String, pageId: String, shotId: String): MockResponse {
        val album = albums[albumId]
        val page = album?.pages?.firstOrNull { it.id == pageId }
        return when (request.method) {
            "PUT" -> {
                val bytes = request.body.readByteArray()
                val claimed = request.getHeader(ServerClient.SHA_HEADER) ?: return status(400)
                if (!sha256(bytes).equals(claimed, true) || !isJpeg(bytes)) return status(400)
                val existing = album?.pages?.flatMap { it.shots }?.firstOrNull { it.id == shotId }
                if (existing != null) return status(if (existing.sha256.equals(claimed, true)) 200 else 409)
                val a = album ?: StoredAlbum(albumId, "Untitled", null, Instant.now().toString()).also { albums[albumId] = it }
                val p = page ?: run {
                    if (a.pages.size >= MAX_PAGES) return status(422)
                    StoredPage(pageId).also { a.pages += it }
                }
                if (p.shots.size >= MAX_SHOTS) return status(422)
                p.shots += StoredShot(shotId, bytes, claimed.lowercase(), Instant.now())
                a.updated = Instant.now()
                status(201)
            }
            "GET" -> {
                val shot = page?.shots?.firstOrNull { it.id == shotId } ?: return status(404)
                if (request.requestUrl!!.queryParameter("size") == "thumb") {
                    MockResponse().setBody(Buffer().write(thumbBytes(shot.id))).addHeader("Content-Type", "image/jpeg")
                } else {
                    val body = shot.bytes.copyOf()
                    if (corruptNextDownloads > 0) {
                        corruptNextDownloads--
                        body[body.size - 1] = (body[body.size - 1].toInt() xor 0x55).toByte()
                    }
                    MockResponse().setBody(Buffer().write(body))
                        .addHeader("Content-Type", "image/jpeg")
                        .addHeader(ServerClient.SHA_HEADER, shot.sha256)
                }
            }
            "DELETE" -> {
                page?.shots?.removeAll { it.id == shotId }
                status(204)
            }
            else -> status(405)
        }
    }

    private fun StoredAlbum.summary() =
        ServerAlbumSummary(id, name, pageSize, pages.size, pages.sumOf { it.shots.size }, updated.toString())

    private fun status(code: Int) = MockResponse().setResponseCode(code)

    private inline fun <reified T> json(value: T) =
        MockResponse().setBody(ServerClient.json.encodeToString(value)).addHeader("Content-Type", "application/json")

    companion object {
        const val MAX_SHOTS = 25
        const val MAX_PAGES = 500

        fun sha256(bytes: ByteArray): String =
            MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }

        fun isJpeg(bytes: ByteArray) = bytes.size >= 4 && bytes[0] == 0xFF.toByte() && bytes[1] == 0xD8.toByte()

        fun thumbBytes(shotId: String) = byteArrayOf(0xFF.toByte(), 0xD8.toByte()) + "thumb:$shotId".toByteArray()

        fun jpeg(seed: String): ByteArray =
            byteArrayOf(0xFF.toByte(), 0xD8.toByte(), 0xFF.toByte(), 0xE0.toByte()) + seed.toByteArray() +
                byteArrayOf(0xFF.toByte(), 0xD9.toByte())
    }
}
