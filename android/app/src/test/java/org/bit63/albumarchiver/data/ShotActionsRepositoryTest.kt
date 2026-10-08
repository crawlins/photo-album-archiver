package org.bit63.albumarchiver.data

import com.google.common.truth.Truth.assertThat
import org.bit63.albumarchiver.TestEnv
import org.bit63.albumarchiver.blocking
import org.junit.After
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import java.io.File
import java.time.Instant

/** The repository side of the shot actions: deleting a run, moving it to the next page, splitting it off. */
@RunWith(RobolectricTestRunner::class)
class ShotActionsRepositoryTest {
    private val env = TestEnv()
    private val repo = env.repo
    private val db = env.db

    @After fun tearDown() = env.close()

    private suspend fun ops() = db.ops().all().map { listOfNotNull(it.kind.name, it.shotId) }
    private suspend fun album() = repo.createAlbum("Family", PageSize.DEFAULT)
    private suspend fun shoot(albumId: String, vararg ids: String) =
        ids.map { (repo.addShot(albumId, it, env.written(it)) as AddShotResult.Added).shot }
    private suspend fun nextPage(albumId: String) = (repo.startNextPage(albumId) as NextPageResult.Started).page
    private suspend fun shotIds(pageId: String) = repo.shots(pageId).map { it.id }
    private suspend fun pageIds(albumId: String) = repo.pages(albumId).map { it.id }

    /** An album with pages of the given shots, e.g. `listOf(listOf("a", "b"), listOf("c"))`. */
    private suspend fun albumOf(vararg pages: List<String>): Pair<Album, List<Page>> {
        val a = album()
        pages.forEachIndexed { i, ids ->
            if (i > 0) nextPage(a.id)
            shoot(a.id, *ids.toTypedArray())
        }
        db.ops().all().forEach { db.ops().delete(it.seq) } // uploaded
        return a to repo.pages(a.id)
    }

    private fun assertFileAt(shot: Shot?, albumId: String, pageId: String) {
        val expected = env.store.shotFile(albumId, pageId, shot!!.id)
        assertThat(shot.path).isEqualTo(expected.absolutePath)
        assertThat(expected.readBytes()).isEqualTo(TestEnv.jpeg(shot.id))
    }

    // ---- runOf ----

    @Test fun `the run is the shot and every later shot of its page`() = blocking {
        albumOf(listOf("a", "b", "c"), listOf("d"))
        assertThat(repo.runOf("b").map { it.id }).containsExactly("b", "c").inOrder()
        assertThat(repo.runOf("c").map { it.id }).containsExactly("c")
        assertThat(repo.runOf("d").map { it.id }).containsExactly("d")
        assertThat(repo.runOf("nope")).isEmpty()
    }

    // ---- Deleting a run ----

    @Test fun `deleting a run removes its rows and files and queues each deletion`() = blocking {
        val (_, pages) = albumOf(listOf("a", "b", "c"))
        val paths = repo.runOf("b").map { it.path }
        assertThat(repo.deleteRun("b")).isEqualTo(RunDeletion(2, pageDeleted = false))
        assertThat(shotIds(pages[0].id)).containsExactly("a")
        assertThat(repo.page(pages[0].id)!!.shotCount).isEqualTo(1)
        assertThat(paths.none { File(it).exists() }).isTrue()
        assertThat(ops()).containsExactly(listOf("DELETE_SHOT", "b"), listOf("DELETE_SHOT", "c")).inOrder()
        assertThat(repo.page(pages[0].id)).isNotNull()
    }

    @Test fun `deleting a run drops its pending uploads`() = blocking {
        val a = album()
        shoot(a.id, "a", "b")
        repo.deleteRun("a")
        assertThat(ops()).containsExactly(
            listOf("ALBUM_META"), listOf("ALBUM_META"), listOf("DELETE_SHOT", "a"), listOf("DELETE_SHOT", "b"),
        ).inOrder()
    }

    @Test fun `deleting every shot of an inner page removes the page and renumbers`() = blocking {
        val (a, pages) = albumOf(listOf("a"), listOf("b", "c"), listOf("d"))
        assertThat(repo.deleteRun("b")).isEqualTo(RunDeletion(2, pageDeleted = true))
        assertThat(pageIds(a.id)).containsExactly(pages[0].id, pages[2].id).inOrder()
        assertThat(repo.pages(a.id).map { it.position }).containsExactly(1, 2).inOrder()
        assertThat(env.store.pageDir(a.id, pages[1].id).exists()).isFalse()
        assertThat(ops().map { it[0] }).containsExactly("DELETE_SHOT", "DELETE_SHOT", "DELETE_PAGE", "ALBUM_META").inOrder()
    }

    @Test fun `deleting every shot of the last page keeps it as the empty current page`() = blocking {
        val (a, pages) = albumOf(listOf("a"), listOf("b", "c"))
        assertThat(repo.deleteRun("b")).isEqualTo(RunDeletion(2, pageDeleted = false))
        assertThat(repo.lastPage(a.id)!!.id).isEqualTo(pages[1].id)
        assertThat(repo.lastPage(a.id)!!.shotCount).isEqualTo(0)
    }

    @Test fun `deleting a missing run does nothing`() = blocking {
        assertThat(repo.deleteRun("nope")).isNull()
    }

    // ---- Moving to the next page ----

    @Test fun `a run moves to the existing next page, ahead of its later shots`() = blocking {
        val (a, pages) = albumOf(listOf("a", "b", "c"), listOf("d"))
        val result = repo.moveRunToNextPage("b") as MoveResult.Moved
        assertThat(result.page.id).isEqualTo(pages[1].id)
        assertThat(result.count).isEqualTo(2)
        assertThat(result.firstShotId).isEqualTo("b")
        assertThat(result.sourceDeleted).isFalse()
        assertThat(shotIds(pages[0].id)).containsExactly("a")
        assertThat(shotIds(pages[1].id)).containsExactly("b", "c", "d").inOrder()
        assertThat(repo.page(pages[0].id)!!.shotCount).isEqualTo(1)
        assertThat(repo.page(pages[1].id)!!.shotCount).isEqualTo(3)
        assertFileAt(repo.shot("b"), a.id, pages[1].id)
        assertThat(env.store.shotFile(a.id, pages[0].id, "b").exists()).isFalse()
        assertThat(ops()).containsExactly(
            listOf("MOVE_SHOTS", "b"), listOf("MOVE_SHOTS", "c"), listOf("ALBUM_META"),
        ).inOrder()
        assertThat(db.ops().all().filter { it.kind == OpKind.MOVE_SHOTS }.map { it.pageId }.toSet()).containsExactly(pages[1].id)
        assertThat(env.kicker.kicks.get()).isGreaterThan(0)
    }

    @Test fun `from the last page a run moves to a new last page, which becomes current`() = blocking {
        val (a, pages) = albumOf(listOf("a"), listOf("b", "c", "d"))
        val result = repo.moveRunToNextPage("c") as MoveResult.Moved
        val newPage = repo.lastPage(a.id)!!
        assertThat(result.page.id).isEqualTo(newPage.id)
        assertThat(newPage.position).isEqualTo(3)
        assertThat(pageIds(a.id)).containsExactly(pages[0].id, pages[1].id, newPage.id).inOrder()
        assertThat(shotIds(newPage.id)).containsExactly("c", "d").inOrder()
        assertThat(shotIds(pages[1].id)).containsExactly("b")
        // the next shot goes to the new page
        assertThat(shoot(a.id, "e").single().pageId).isEqualTo(newPage.id)
        val meta = db.ops().all().first { it.kind == OpKind.ALBUM_META }
        assertThat(meta.pages).isEqualTo(listOf(pages[0].id, pages[1].id, newPage.id).joinToString(","))
    }

    @Test fun `moving a whole inner page into the next one removes it and renumbers`() = blocking {
        val (a, pages) = albumOf(listOf("a"), listOf("b", "c"), listOf("d"), listOf("e"))
        val result = repo.moveRunToNextPage("b") as MoveResult.Moved
        assertThat(result.sourceDeleted).isTrue()
        assertThat(result.page.id).isEqualTo(pages[2].id)
        assertThat(result.page.position).isEqualTo(2)
        assertThat(pageIds(a.id)).containsExactly(pages[0].id, pages[2].id, pages[3].id).inOrder()
        assertThat(repo.pages(a.id).map { it.position }).containsExactly(1, 2, 3).inOrder()
        assertThat(shotIds(pages[2].id)).containsExactly("b", "c", "d").inOrder()
        assertFileAt(repo.shot("c"), a.id, pages[2].id)
        assertThat(env.store.pageDir(a.id, pages[1].id).exists()).isFalse()
        assertThat(ops().map { it[0] }).containsExactly("MOVE_SHOTS", "MOVE_SHOTS", "DELETE_PAGE", "ALBUM_META").inOrder()
    }

    @Test fun `moving the whole last page changes nothing`() = blocking {
        val (a, pages) = albumOf(listOf("a"), listOf("b", "c"))
        assertThat(repo.moveRunToNextPage("b")).isEqualTo(MoveResult.NothingToMove)
        assertThat(pageIds(a.id)).isEqualTo(pages.map { it.id })
        assertThat(ops()).isEmpty()
    }

    @Test fun `a move that would put more than 25 shots on the next page is refused`() = blocking {
        val (_, pages) = albumOf(listOf("a", "b"), (1..24).map { "n$it" })
        assertThat(repo.moveRunToNextPage("a")).isEqualTo(MoveResult.PageFull)
        assertThat(shotIds(pages[0].id)).containsExactly("a", "b")
        assertThat(ops()).isEmpty()
        assertThat(repo.moveRunToNextPage("b")).isInstanceOf(MoveResult.Moved::class.java)
        assertThat(repo.page(pages[1].id)!!.shotCount).isEqualTo(25)
    }

    @Test fun `a new page is refused when the album has 500 pages`() = blocking {
        val a = album()
        for (i in 1..Limits.MAX_PAGES_PER_ALBUM) {
            if (i > 1) nextPage(a.id)
            shoot(a.id, "s$i")
        }
        shoot(a.id, "extra")
        assertThat(repo.moveRunToNextPage("extra")).isEqualTo(MoveResult.AlbumFull)
        assertThat(repo.splitRunToNewPage("s1")).isEqualTo(MoveResult.NothingToMove)
        assertThat(repo.splitRunToNewPage("extra")).isEqualTo(MoveResult.AlbumFull)
        assertThat(repo.pages(a.id)).hasSize(Limits.MAX_PAGES_PER_ALBUM)
    }

    @Test fun `a moved shot still waiting to upload is uploaded to its new page and moved too`() = blocking {
        val a = album()
        shoot(a.id, "a", "b", "c")
        val result = repo.moveRunToNextPage("b") as MoveResult.Moved
        val all = db.ops().all()
        val puts = all.filter { it.kind == OpKind.PUT_SHOT }
        assertThat(puts.map { it.shotId to it.pageId }).containsExactly(
            "a" to repo.shot("a")!!.pageId, "b" to result.page.id, "c" to result.page.id,
        ).inOrder()
        assertThat(all.filter { it.kind == OpKind.MOVE_SHOTS }.map { it.shotId }).containsExactly("b", "c").inOrder()
        // queued after the uploads they follow
        assertThat(all.indexOfFirst { it.kind == OpKind.MOVE_SHOTS }).isGreaterThan(all.indexOfLast { it.kind == OpKind.PUT_SHOT })
    }

    @Test fun `pending uploads survive the removal of the page their shots left`() = blocking {
        val a = album()
        shoot(a.id, "a")
        val p2 = nextPage(a.id)
        shoot(a.id, "b", "c")
        nextPage(a.id)
        shoot(a.id, "d")
        val result = repo.moveRunToNextPage("b") as MoveResult.Moved
        assertThat(result.sourceDeleted).isTrue()
        val puts = db.ops().all().filter { it.kind == OpKind.PUT_SHOT }.map { it.shotId }
        assertThat(puts).containsExactly("a", "b", "c", "d").inOrder()
        assertThat(repo.page(p2.id)).isNull()
    }

    // ---- Splitting a run off ----

    @Test fun `splitting an inner page inserts a page after it and renumbers the rest`() = blocking {
        val (a, pages) = albumOf(listOf("a", "b", "c"), listOf("d"))
        val result = repo.splitRunToNewPage("b") as MoveResult.Moved
        val after = repo.pages(a.id)
        assertThat(after.map { it.position }).containsExactly(1, 2, 3).inOrder()
        assertThat(after.map { it.id }).containsExactly(pages[0].id, result.page.id, pages[1].id).inOrder()
        assertThat(result.page.position).isEqualTo(2)
        assertThat(shotIds(result.page.id)).containsExactly("b", "c").inOrder()
        assertThat(shotIds(pages[1].id)).containsExactly("d")
        assertThat(repo.lastPage(a.id)!!.id).isEqualTo(pages[1].id)
        assertFileAt(repo.shot("c"), a.id, result.page.id)
        assertThat(ops().map { it[0] }).containsExactly("MOVE_SHOTS", "MOVE_SHOTS", "ALBUM_META").inOrder()
    }

    @Test fun `splitting the last page makes the new page current`() = blocking {
        val (a, _) = albumOf(listOf("a", "b"))
        val result = repo.splitRunToNewPage("b") as MoveResult.Moved
        assertThat(repo.lastPage(a.id)!!.id).isEqualTo(result.page.id)
        assertThat(result.page.position).isEqualTo(2)
    }

    @Test fun `splitting a whole page changes nothing`() = blocking {
        val (a, pages) = albumOf(listOf("a", "b"), listOf("c"))
        assertThat(repo.splitRunToNewPage("a")).isEqualTo(MoveResult.NothingToMove)
        assertThat(pageIds(a.id)).isEqualTo(pages.map { it.id })
        assertThat(ops()).isEmpty()
    }

    @Test fun `moving a missing shot does nothing`() = blocking {
        assertThat(repo.moveRunToNextPage("nope")).isEqualTo(MoveResult.NotFound)
        assertThat(repo.splitRunToNewPage("nope")).isEqualTo(MoveResult.NotFound)
    }

    @Test fun `shots that were never fetched move without a file`() = blocking {
        repo.importAlbum(Album("srv", "Server", "a4", Instant.EPOCH), listOf("p1" to 3, "p2" to 1))
        val t = Instant.parse("2026-01-01T00:00:00Z")
        repo.recordPageShots("p1", (0..2).map { RemoteShot("r$it", "h$it", 1, t.plusSeconds(it.toLong())) })
        val result = repo.moveRunToNextPage("r1") as MoveResult.Moved
        assertThat(result.page.id).isEqualTo("p2")
        assertThat(repo.page("p2")!!.shotCount).isEqualTo(3)
        assertThat(repo.shot("r1")!!.state).isEqualTo(ShotState.NOT_DOWNLOADED)
        assertThat(repo.shot("r1")!!.path).isEqualTo(env.store.shotFile("srv", "p2", "r1").absolutePath)
        assertThat(ops()).containsExactly(listOf("MOVE_SHOTS", "r1"), listOf("MOVE_SHOTS", "r2"), listOf("ALBUM_META")).inOrder()
    }

    @Test fun `the 25 shot limit counts server shots of an unloaded next page`() = blocking {
        repo.importAlbum(Album("srv", "Server", "a4", Instant.EPOCH), listOf("p1" to 2, "p2" to 24))
        repo.recordPageShots("p1", listOf(RemoteShot("x", "h", 1, Instant.EPOCH), RemoteShot("y", "h", 1, Instant.EPOCH.plusSeconds(1))))
        assertThat(repo.moveRunToNextPage("x")).isEqualTo(MoveResult.PageFull)
        assertThat(repo.moveRunToNextPage("y")).isInstanceOf(MoveResult.Moved::class.java)
    }

    // ---- Crashes ----

    @Test fun `startup cleanup after a move keeps every shot's file`() = blocking {
        val (a, pages) = albumOf(listOf("a", "b"), listOf("c"))
        // A crash before the commit: a second name in the target folder, rows unchanged.
        val stray = env.store.shotFile(a.id, pages[1].id, "b")
        env.store.link(File(repo.shot("b")!!.path), stray)
        env.store.cleanUp(repo.knownPaths())
        assertThat(stray.exists()).isFalse()
        assertFileAt(repo.shot("b"), a.id, pages[0].id)

        // A crash after the commit: the old name is still there.
        val old = repo.shot("b")!!.path
        repo.moveRunToNextPage("b")
        env.store.link(env.store.shotFile(a.id, pages[1].id, "b"), File(old))
        env.store.cleanUp(repo.knownPaths())
        assertThat(File(old).exists()).isFalse()
        assertFileAt(repo.shot("b"), a.id, pages[1].id)
    }
}
