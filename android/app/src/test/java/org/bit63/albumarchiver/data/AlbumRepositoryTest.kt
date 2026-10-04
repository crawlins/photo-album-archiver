package org.bit63.albumarchiver.data

import com.google.common.truth.Truth.assertThat
import kotlinx.coroutines.flow.first
import org.bit63.albumarchiver.blocking
import org.bit63.albumarchiver.TestEnv
import org.junit.After
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import java.io.File
import java.time.Instant

@RunWith(RobolectricTestRunner::class)
class AlbumRepositoryTest {
    private val env = TestEnv()
    private val repo = env.repo
    private val db = env.db

    @After fun tearDown() = env.close()

    private suspend fun ops() = db.ops().all().map { listOfNotNull(it.kind.name, it.shotId) }
    private suspend fun kinds() = db.ops().all().map { it.kind }

    private suspend fun album(name: String = "Family") = repo.createAlbum(name, PageSize.DEFAULT)

    private suspend fun shoot(albumId: String, id: String): AddShotResult = repo.addShot(albumId, id, env.written(id))

    private suspend fun added(albumId: String, id: String) = shoot(albumId, id) as AddShotResult.Added

    private suspend fun positions(albumId: String) = repo.pages(albumId).map { it.position }

    // ---- Albums ----

    @Test fun `createAlbum stores a trimmed name and page size and queues its metadata`() = blocking {
        val a = repo.createAlbum("  Rawlins 1962  ", PageSize.Preset.A4)
        assertThat(repo.album(a.id)).isEqualTo(a)
        assertThat(a.name).isEqualTo("Rawlins 1962")
        assertThat(a.pageSize).isEqualTo("a4")
        assertThat(repo.pages(a.id)).isEmpty()
        assertThat(kinds()).containsExactly(OpKind.ALBUM_META)
        assertThat(env.kicker.kicks.get()).isEqualTo(1)
    }

    @Test fun `updateAlbum changes name and size, keeps pages and shots, and queues metadata`() = blocking {
        val a = album()
        added(a.id, "s1")
        repo.startNextPage(a.id)
        added(a.id, "s2")
        val pagesBefore = repo.pages(a.id)
        val shotsBefore = pagesBefore.flatMap { repo.shots(it.id) }

        assertThat(repo.updateAlbum(a.id, "Renamed", PageSize.Custom(10.toBigDecimal(), 12.toBigDecimal(), PageSize.Unit.IN))).isTrue()

        val updated = repo.album(a.id)!!
        assertThat(updated.name).isEqualTo("Renamed")
        assertThat(updated.pageSize).isEqualTo("10x12in")
        assertThat(updated.createdAt).isEqualTo(a.createdAt)
        assertThat(repo.pages(a.id)).isEqualTo(pagesBefore)
        assertThat(repo.pages(a.id).flatMap { repo.shots(it.id) }).isEqualTo(shotsBefore)
        assertThat(kinds().last()).isEqualTo(OpKind.ALBUM_META)
    }

    @Test fun `updateAlbum on a missing album does nothing`() = blocking {
        assertThat(repo.updateAlbum("nope", "x", PageSize.DEFAULT)).isFalse()
        assertThat(ops()).isEmpty()
    }

    @Test fun `deleteAlbum removes rows, files and pending uploads without telling the server`() = blocking {
        val a = album()
        val other = album("Other")
        val shot = added(a.id, "s1").shot
        added(other.id, "o1")
        repo.deleteAlbum(a.id, alsoOnServer = false)

        assertThat(repo.album(a.id)).isNull()
        assertThat(repo.pages(a.id)).isEmpty()
        assertThat(repo.shot("s1")).isNull()
        assertThat(File(shot.path).exists()).isFalse()
        assertThat(env.store.albumDir(a.id).exists()).isFalse()
        assertThat(db.ops().all().map { it.albumId }.toSet()).containsExactly(other.id)
    }

    @Test fun `deleteAlbum also on the server drops pending uploads and queues one album deletion`() = blocking {
        val a = album()
        added(a.id, "s1")
        added(a.id, "s2")
        repo.deleteAlbum(a.id, alsoOnServer = true)
        val remaining = db.ops().all()
        assertThat(remaining.map { it.kind }).containsExactly(OpKind.DELETE_ALBUM)
        assertThat(remaining.single().albumId).isEqualTo(a.id)
    }

    @Test fun `summaries count pages and pending ops`() = blocking {
        val a = album()
        added(a.id, "s1")
        repo.startNextPage(a.id)
        val s = repo.observeSummaries().first().single()
        assertThat(s.pageCount).isEqualTo(2)
        assertThat(s.pendingOps).isEqualTo(4) // meta, page 1 meta, shot, page 2 meta
    }

    // ---- Shots ----

    @Test fun `first shot creates page 1 and queues metadata before the shot`() = blocking {
        val a = album()
        val r = added(a.id, "s1")
        assertThat(r.pageCreated).isTrue()
        assertThat(r.page.position).isEqualTo(1)
        assertThat(r.page.shotCount).isEqualTo(1)
        assertThat(ops()).containsExactly(listOf("ALBUM_META"), listOf("ALBUM_META"), listOf("PUT_SHOT", "s1")).inOrder()
    }

    @Test fun `a shot is moved into its page folder and recorded with its hash and size`() = blocking {
        val a = album()
        val bytes = TestEnv.jpeg("s1")
        val shot = repo.addShot(a.id, "s1", env.written("s1", bytes)).let { (it as AddShotResult.Added).shot }
        val file = File(shot.path)
        assertThat(file).isEqualTo(env.store.shotFile(a.id, shot.pageId, "s1"))
        assertThat(file.readBytes()).isEqualTo(bytes)
        assertThat(shot.bytes).isEqualTo(bytes.size.toLong())
        assertThat(shot.sha256).isEqualTo(ShotStore.sha256(bytes.inputStream()))
        assertThat(shot.state).isEqualTo(ShotState.PRESENT)
    }

    @Test fun `later shots go to the current last page`() = blocking {
        val a = album()
        val p1 = added(a.id, "s1").page
        assertThat(added(a.id, "s2").page.id).isEqualTo(p1.id)
        val p2 = (repo.startNextPage(a.id) as NextPageResult.Started).page
        assertThat(added(a.id, "s3").page.id).isEqualTo(p2.id)
        assertThat(repo.shots(p1.id).map { it.id }).containsExactly("s1", "s2").inOrder()
    }

    @Test fun `shots are listed in the order they were taken`() = blocking {
        val a = album()
        val ids = (1..6).map { "shot-$it" }.shuffled(java.util.Random(4))
        ids.forEach { added(a.id, it) }
        val page = repo.lastPage(a.id)!!
        assertThat(repo.shots(page.id).map { it.id }).isEqualTo(ids)
    }

    @Test fun `the 26th shot of a page is refused and leaves its temp file for the caller`() = blocking {
        val a = album()
        repeat(Limits.MAX_SHOTS_PER_PAGE) { added(a.id, "s$it") }
        val w = env.written("s25")
        assertThat(repo.addShot(a.id, "s25", w)).isEqualTo(AddShotResult.PageFull)
        assertThat(w.file.exists()).isTrue()
        assertThat(repo.lastPage(a.id)!!.shotCount).isEqualTo(25)
        assertThat(db.ops().all().count { it.kind == OpKind.PUT_SHOT }).isEqualTo(25)
    }

    @Test fun `a shot for a missing album is refused`() = blocking {
        assertThat(shoot("nope", "s1")).isEqualTo(AddShotResult.NoAlbum)
    }

    // ---- Next page ----

    @Test fun `next page on a page with shots starts the next number and queues metadata after the shots`() = blocking {
        val a = album()
        added(a.id, "s1")
        added(a.id, "s2")
        val r = repo.startNextPage(a.id) as NextPageResult.Started
        assertThat(r.page.position).isEqualTo(2)
        assertThat(r.page.shotCount).isEqualTo(0)
        assertThat(ops().takeLast(3)).containsExactly(listOf("PUT_SHOT", "s1"), listOf("PUT_SHOT", "s2"), listOf("ALBUM_META")).inOrder()
    }

    @Test fun `next page is refused on an empty page, so empty pages are never created`() = blocking {
        val a = album()
        assertThat(repo.startNextPage(a.id)).isEqualTo(NextPageResult.CurrentPageEmpty)
        added(a.id, "s1")
        repo.startNextPage(a.id)
        assertThat(repo.startNextPage(a.id)).isEqualTo(NextPageResult.CurrentPageEmpty)
        assertThat(positions(a.id)).containsExactly(1, 2).inOrder()
    }

    @Test fun `next page on a missing album`() = blocking {
        assertThat(repo.startNextPage("nope")).isEqualTo(NextPageResult.NoAlbum)
    }

    @Test fun `page 501 is refused`() = blocking {
        val a = album()
        // Build 500 pages with a shot each directly, which is much faster than 500 transactions of each kind.
        db.pages().insertAll((1..Limits.MAX_PAGES_PER_ALBUM).map { Page("p$it", a.id, it, shotCount = 1) })
        assertThat(repo.startNextPage(a.id)).isEqualTo(NextPageResult.AlbumFull)
        assertThat(repo.pages(a.id)).hasSize(500)
    }

    @Test fun `undo removes a new empty page and queues metadata`() = blocking {
        val a = album()
        added(a.id, "s1")
        val p2 = (repo.startNextPage(a.id) as NextPageResult.Started).page
        assertThat(repo.undoNextPage(p2.id)).isTrue()
        assertThat(positions(a.id)).containsExactly(1)
        assertThat(kinds().last()).isEqualTo(OpKind.ALBUM_META)
        assertThat(added(a.id, "s2").page.position).isEqualTo(1)
    }

    @Test fun `undo is refused once the new page has a shot or is gone`() = blocking {
        val a = album()
        added(a.id, "s1")
        val p2 = (repo.startNextPage(a.id) as NextPageResult.Started).page
        added(a.id, "s2")
        assertThat(repo.undoNextPage(p2.id)).isFalse()
        assertThat(repo.undoNextPage("nope")).isFalse()
        assertThat(positions(a.id)).containsExactly(1, 2)
    }

    @Test fun `undo is refused for a page that is no longer the last`() = blocking {
        val a = album()
        added(a.id, "s1")
        val p1 = repo.lastPage(a.id)!!
        repo.deleteShot("s1") // p1 is the last page, so it stays, empty
        assertThat(repo.pages(a.id).single().id).isEqualTo(p1.id)
        added(a.id, "s2")
        repo.startNextPage(a.id)
        assertThat(repo.undoNextPage(p1.id)).isFalse()
    }

    // ---- Deleting shots ----

    @Test fun `deleting a shot removes its row and file, drops its pending upload and queues the deletion`() = blocking {
        val a = album()
        val shot = added(a.id, "s1").shot
        added(a.id, "s2")
        val result = repo.deleteShot("s1")
        assertThat(result).isEqualTo(ShotDeletion(pageDeleted = false))
        assertThat(repo.shot("s1")).isNull()
        assertThat(File(shot.path).exists()).isFalse()
        assertThat(repo.lastPage(a.id)!!.shotCount).isEqualTo(1)
        assertThat(ops()).doesNotContain(listOf("PUT_SHOT", "s1"))
        assertThat(ops().last()).isEqualTo(listOf("DELETE_SHOT", "s1"))
    }

    @Test fun `deleting the only shot of an inner page deletes the page and renumbers`() = blocking {
        val a = album()
        added(a.id, "s1")
        repo.startNextPage(a.id)
        val p2 = added(a.id, "s2").page
        repo.startNextPage(a.id)
        val p3 = added(a.id, "s3").page

        assertThat(repo.deleteShot("s2")).isEqualTo(ShotDeletion(pageDeleted = true))

        val pages = repo.pages(a.id)
        assertThat(pages.map { it.position }).containsExactly(1, 2).inOrder()
        assertThat(pages.last().id).isEqualTo(p3.id)
        assertThat(repo.page(p2.id)).isNull()
        assertThat(env.store.pageDir(a.id, p2.id).exists()).isFalse()
        assertThat(kinds().takeLast(3)).containsExactly(OpKind.DELETE_SHOT, OpKind.DELETE_PAGE, OpKind.ALBUM_META).inOrder()
    }

    @Test fun `deleting the only shot of the last page keeps the page as the current page`() = blocking {
        val a = album()
        added(a.id, "s1")
        repo.startNextPage(a.id)
        val p2 = added(a.id, "s2").page
        assertThat(repo.deleteShot("s2")).isEqualTo(ShotDeletion(pageDeleted = false))
        assertThat(repo.lastPage(a.id)!!.id).isEqualTo(p2.id)
        assertThat(repo.lastPage(a.id)!!.shotCount).isEqualTo(0)
        assertThat(added(a.id, "s3").page.id).isEqualTo(p2.id)
    }

    @Test fun `deleting a missing shot does nothing`() = blocking {
        assertThat(repo.deleteShot("nope")).isNull()
    }

    @Test fun `deleted shots free room under the 25 shot limit`() = blocking {
        val a = album()
        repeat(Limits.MAX_SHOTS_PER_PAGE) { added(a.id, "s$it") }
        assertThat(shoot(a.id, "extra")).isEqualTo(AddShotResult.PageFull)
        repo.deleteShot("s3")
        assertThat(shoot(a.id, "extra2")).isInstanceOf(AddShotResult.Added::class.java)
    }

    // ---- Deleting pages ----

    @Test fun `deleting a middle page renumbers the later pages and keeps their ids`() = blocking {
        val a = album()
        val ids = mutableListOf<String>()
        for (i in 1..5) {
            if (i > 1) repo.startNextPage(a.id)
            ids += added(a.id, "s$i").page.id
        }
        assertThat(repo.deletePage(ids[1])).isTrue()

        val pages = repo.pages(a.id)
        assertThat(pages.map { it.position }).containsExactly(1, 2, 3, 4).inOrder()
        assertThat(pages.map { it.id }).containsExactly(ids[0], ids[2], ids[3], ids[4]).inOrder()
        assertThat(repo.shot("s2")).isNull()
        assertThat(repo.shot("s3")!!.pageId).isEqualTo(ids[2])
        assertThat(File(repo.shot("s3")!!.path).exists()).isTrue()
    }

    @Test fun `deleting a page drops its pending uploads and queues page deletion and metadata`() = blocking {
        val a = album()
        added(a.id, "s1")
        repo.startNextPage(a.id)
        val p2 = added(a.id, "s2").page
        added(a.id, "s3")
        repo.deletePage(p2.id)
        assertThat(ops()).doesNotContain(listOf("PUT_SHOT", "s2"))
        assertThat(ops()).doesNotContain(listOf("PUT_SHOT", "s3"))
        assertThat(ops()).contains(listOf("PUT_SHOT", "s1"))
        val tail = db.ops().all().takeLast(2)
        assertThat(tail.map { it.kind }).containsExactly(OpKind.DELETE_PAGE, OpKind.ALBUM_META).inOrder()
        assertThat(tail.first().pageId).isEqualTo(p2.id)
    }

    @Test fun `deleting the last page makes the previous page current`() = blocking {
        val a = album()
        val p1 = added(a.id, "s1").page
        repo.startNextPage(a.id)
        val p2 = added(a.id, "s2").page
        repo.deletePage(p2.id)
        assertThat(repo.lastPage(a.id)!!.id).isEqualTo(p1.id)
        assertThat(added(a.id, "s3").page.id).isEqualTo(p1.id)
    }

    @Test fun `deleting every page leaves an album with no pages and the next shot makes page 1`() = blocking {
        val a = album()
        val p1 = added(a.id, "s1").page
        repo.deletePage(p1.id)
        assertThat(repo.pages(a.id)).isEmpty()
        val r = added(a.id, "s2")
        assertThat(r.pageCreated).isTrue()
        assertThat(r.page.position).isEqualTo(1)
    }

    @Test fun `deleted pages free room under the 500 page limit`() = blocking {
        val a = album()
        db.pages().insertAll((1..Limits.MAX_PAGES_PER_ALBUM).map { Page("p$it", a.id, it, shotCount = 1) })
        assertThat(repo.startNextPage(a.id)).isEqualTo(NextPageResult.AlbumFull)
        repo.deletePage("p10")
        assertThat(repo.startNextPage(a.id)).isInstanceOf(NextPageResult.Started::class.java)
        assertThat(positions(a.id)).isEqualTo((1..500).toList())
    }

    @Test fun `deleting a missing page does nothing`() = blocking {
        assertThat(repo.deletePage("nope")).isFalse()
    }

    // ---- Server albums ----

    @Test fun `importing an album writes unloaded pages with server counts and queues nothing`() = blocking {
        val album = Album("srv", "Server", "a4", Instant.parse("2026-01-01T00:00:00Z"))
        repo.importAlbum(album, listOf("p1" to 3, "p2" to 4))
        val pages = repo.pages("srv")
        assertThat(pages.map { Triple(it.position, it.shotCount, it.shotsLoaded) })
            .containsExactly(Triple(1, 3, false), Triple(2, 4, false)).inOrder()
        assertThat(ops()).isEmpty()
        assertThat(env.kicker.kicks.get()).isEqualTo(0)
    }

    @Test fun `recording a page's shots adds not-downloaded rows and marks the page loaded`() = blocking {
        repo.importAlbum(Album("srv", "Server", "a4", Instant.EPOCH), listOf("p1" to 2))
        val t = Instant.parse("2026-01-01T00:00:00Z")
        repo.recordPageShots("p1", listOf(RemoteShot("b", "hb", 2, t.plusSeconds(1)), RemoteShot("a", "ha", 1, t)))
        val shots = repo.shots("p1")
        assertThat(shots.map { it.id }).containsExactly("a", "b").inOrder()
        assertThat(shots.all { it.state == ShotState.NOT_DOWNLOADED }).isTrue()
        assertThat(shots.first().path).isEqualTo(env.store.shotFile("srv", "p1", "a").absolutePath)
        assertThat(repo.page("p1")!!.shotsLoaded).isTrue()
        assertThat(repo.page("p1")!!.shotCount).isEqualTo(2)
        // Recording again is harmless.
        repo.recordPageShots("p1", listOf(RemoteShot("a", "ha", 1, t)))
        assertThat(repo.shots("p1")).hasSize(2)
    }

    @Test fun `deleting an unfetched shot or page queues the server deletion like any other`() = blocking {
        repo.importAlbum(Album("srv", "Server", "a4", Instant.EPOCH), listOf("p1" to 1, "p2" to 2, "p3" to 1))
        repo.recordPageShots("p2", listOf(RemoteShot("x", "h", 1, Instant.EPOCH), RemoteShot("y", "h", 1, Instant.EPOCH)))
        repo.deleteShot("x")
        repo.deletePage("p1")
        assertThat(ops()).containsExactly(
            listOf("DELETE_SHOT", "x"), listOf("DELETE_PAGE"), listOf("ALBUM_META"),
        ).inOrder()
        assertThat(positions("srv")).containsExactly(1, 2)
    }

    @Test fun `new shots on an opened album are uploaded, fetched ones are not`() = blocking {
        repo.importAlbum(Album("srv", "Server", "a4", Instant.EPOCH), listOf("p1" to 1))
        repo.recordPageShots("p1", listOf(RemoteShot("old", "h", 1, Instant.EPOCH)))
        repo.markShotPresent("old")
        added("srv", "new")
        assertThat(ops()).containsExactly(listOf("PUT_SHOT", "new"))
        assertThat(repo.page("p1")!!.shotCount).isEqualTo(2)
    }

    @Test fun `the shot limit counts server shots of an unloaded page`() = blocking {
        repo.importAlbum(Album("srv", "Server", "a4", Instant.EPOCH), listOf("p1" to 25))
        assertThat(shoot("srv", "s")).isEqualTo(AddShotResult.PageFull)
    }

    @Test fun `forgetting a shot missing on the server updates the count without queuing`() = blocking {
        repo.importAlbum(Album("srv", "Server", "a4", Instant.EPOCH), listOf("p1" to 2))
        repo.recordPageShots("p1", listOf(RemoteShot("a", "h", 1, Instant.EPOCH), RemoteShot("b", "h", 1, Instant.EPOCH)))
        repo.forgetMissingShot("a")
        assertThat(repo.page("p1")!!.shotCount).isEqualTo(1)
        assertThat(ops()).isEmpty()
    }

    @Test fun `knownPaths lists every shot file`() = blocking {
        val a = album()
        val s = added(a.id, "s1").shot
        assertThat(repo.knownPaths()).containsExactly(File(s.path).absolutePath)
    }
}
