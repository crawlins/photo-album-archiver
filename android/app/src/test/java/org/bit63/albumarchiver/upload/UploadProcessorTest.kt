package org.bit63.albumarchiver.upload

import com.google.common.truth.Truth.assertThat
import kotlinx.coroutines.flow.first
import org.bit63.albumarchiver.TestEnv
import org.bit63.albumarchiver.blocking
import org.bit63.albumarchiver.data.AddShotResult
import org.bit63.albumarchiver.data.MoveResult
import org.bit63.albumarchiver.data.NextPageResult
import org.bit63.albumarchiver.data.OpKind
import org.bit63.albumarchiver.data.PageSize
import org.bit63.albumarchiver.data.UploadOp
import org.bit63.albumarchiver.testing.FakeAlbumServer
import org.junit.After
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import java.io.File

@RunWith(RobolectricTestRunner::class)
class UploadProcessorTest {
    private val fake = FakeAlbumServer().start()
    private val env = TestEnv(fake.url, fake.token)
    private val processor = UploadProcessor(env.db, env.settings, ServerAccess(env.settings))
    private val repo = env.repo

    @After fun tearDown() {
        fake.shutdown()
        env.close()
    }

    private suspend fun album(name: String = "Family") = repo.createAlbum(name, PageSize.DEFAULT)
    private suspend fun shoot(albumId: String, id: String) = (repo.addShot(albumId, id, env.written(id)) as AddShotResult.Added)
    private suspend fun queue() = env.db.ops().all()
    private fun requests() = fake.log.map { it.substringBefore(" ") + " " + it.substringAfter("/api/v1/") }

    @Test fun `metadata reaches the server before a new album's first shot`() = blocking {
        val a = album()
        val shot = shoot(a.id, "s1")
        assertThat(processor.drain()).isEqualTo(UploadProcessor.Outcome.DONE)
        assertThat(requests()).containsExactly(
            "PUT albums/${a.id}",
            "PUT albums/${a.id}",
            "PUT albums/${a.id}/pages/${shot.page.id}/shots/s1",
        ).inOrder()
        val stored = fake.album(a.id)!!
        assertThat(stored.name).isEqualTo("Family")
        assertThat(stored.pageSize).isEqualTo("letter")
        assertThat(stored.pages.map { it.id }).containsExactly(shot.page.id)
        assertThat(stored.pages.single().shots.single().bytes).isEqualTo(File(shot.shot.path).readBytes())
        assertThat(queue()).isEmpty()
    }

    @Test fun `a new page's metadata is sent only after every shot of the page before it`() = blocking {
        val a = album()
        shoot(a.id, "s1")
        shoot(a.id, "s2")
        val p2 = (repo.startNextPage(a.id) as NextPageResult.Started).page
        shoot(a.id, "s3")
        processor.drain()
        val log = requests()
        val metaForPage2 = log.indexOfLast { it == "PUT albums/${a.id}" }
        assertThat(log.indexOfFirst { it.endsWith("shots/s1") }).isLessThan(metaForPage2)
        assertThat(log.indexOfFirst { it.endsWith("shots/s2") }).isLessThan(metaForPage2)
        assertThat(log.indexOfFirst { it.endsWith("shots/s3") }).isGreaterThan(metaForPage2)
        assertThat(fake.album(a.id)!!.pages.map { it.id }.last()).isEqualTo(p2.id)
    }

    @Test fun `each metadata update lists the pages as they were when it was queued`() = blocking {
        // Offline: shoot, edit the album, shoot again, then "Next page".
        val a = album()
        val p1 = shoot(a.id, "s1").page
        repo.updateAlbum(a.id, "Renamed", PageSize.DEFAULT)
        shoot(a.id, "s2")
        val p2 = (repo.startNextPage(a.id) as NextPageResult.Started).page
        processor.drain()
        assertThat(requests()).containsExactly(
            "PUT albums/${a.id}",
            "PUT albums/${a.id}",
            "PUT albums/${a.id}/pages/${p1.id}/shots/s1",
            "PUT albums/${a.id}",
            "PUT albums/${a.id}/pages/${p1.id}/shots/s2",
            "PUT albums/${a.id}",
        ).inOrder()
        // Page 2 is announced only by the last update, after both of page 1's shots.
        assertThat(fake.metadataPages).containsExactly(
            listOf<String>(),
            listOf(p1.id),
            listOf(p1.id),
            listOf(p1.id, p2.id),
        ).inOrder()
    }

    @Test fun `metadata is the album's latest state when sent`() = blocking {
        val a = album()
        repo.updateAlbum(a.id, "Renamed", PageSize.Preset.A3)
        processor.drain()
        assertThat(fake.album(a.id)!!.name).isEqualTo("Renamed")
        assertThat(fake.album(a.id)!!.pageSize).isEqualTo("a3")
    }

    @Test fun `5xx and 507 are retried, the failed op blocks the ones behind it, and attempts count`() = blocking {
        val a = album()
        shoot(a.id, "s1")
        fake.failNext += 503
        assertThat(processor.drain()).isEqualTo(UploadProcessor.Outcome.RETRY)
        assertThat(queue().map { it.kind }).containsExactly(OpKind.ALBUM_META, OpKind.ALBUM_META, OpKind.PUT_SHOT).inOrder()
        assertThat(queue().first().attempts).isEqualTo(1)
        assertThat(fake.log).hasSize(1)

        fake.failNext += 507
        assertThat(processor.drain()).isEqualTo(UploadProcessor.Outcome.RETRY)
        assertThat(processor.drain()).isEqualTo(UploadProcessor.Outcome.DONE)
        assertThat(fake.album(a.id)!!.pages.single().shots).hasSize(1)
    }

    @Test fun `a network failure is retried`() = blocking {
        album()
        env.settings.setServer("http://127.0.0.1:1", fake.token)
        assertThat(processor.drain()).isEqualTo(UploadProcessor.Outcome.RETRY)
        assertThat(queue()).hasSize(1)
    }

    @Test fun `a retried upload sends the same id and hash, and the server keeps one copy`() = blocking {
        val a = album()
        val s = shoot(a.id, "s1")
        processor.drain()
        // The server stored it but the confirmation was lost, so the op is still queued.
        env.db.ops().insert(UploadOp(kind = OpKind.PUT_SHOT, albumId = a.id, pageId = s.page.id, shotId = "s1"))
        assertThat(processor.drain()).isEqualTo(UploadProcessor.Outcome.DONE)
        val puts = fake.log.filter { it.endsWith("/shots/s1") }
        assertThat(puts).hasSize(2)
        assertThat(fake.album(a.id)!!.pages.single().shots).hasSize(1)
    }

    @Test fun `401 pauses the queue until the server settings change`() = blocking {
        val a = album()
        shoot(a.id, "s1")
        fake.token = "rotated"
        assertThat(processor.drain()).isEqualTo(UploadProcessor.Outcome.AUTH_FAILED)
        assertThat(env.settings.authRejected.first()).isTrue()
        assertThat(queue()).hasSize(3)

        val before = fake.log.size
        assertThat(processor.drain()).isEqualTo(UploadProcessor.Outcome.AUTH_FAILED)
        assertThat(fake.log.size).isEqualTo(before)

        env.settings.setServer(fake.url, "rotated")
        assertThat(env.settings.authRejected.first()).isFalse()
        assertThat(processor.drain()).isEqualTo(UploadProcessor.Outcome.DONE)
    }

    @Test fun `403 is treated like 401`() = blocking {
        album()
        fake.failNext += 403
        assertThat(processor.drain()).isEqualTo(UploadProcessor.Outcome.AUTH_FAILED)
    }

    @Test fun `with no server configured nothing is sent and ops stay queued`() = blocking {
        val a = album()
        shoot(a.id, "s1")
        env.settings.setServer("", "")
        assertThat(processor.drain()).isEqualTo(UploadProcessor.Outcome.NO_SERVER)
        assertThat(queue()).hasSize(3)
        assertThat(fake.log).isEmpty()
    }

    @Test fun `a split page reaches the server as moves before the new page order`() = blocking {
        val a = album()
        val p1 = shoot(a.id, "s1").page
        shoot(a.id, "s2")
        processor.drain()
        fake.log.clear()
        val moved = repo.splitRunToNewPage("s2") as MoveResult.Moved
        assertThat(processor.drain()).isEqualTo(UploadProcessor.Outcome.DONE)
        assertThat(requests()).containsExactly(
            "POST albums/${a.id}/pages/${moved.page.id}/move",
            "PUT albums/${a.id}",
        ).inOrder()
        val stored = fake.album(a.id)!!
        assertThat(stored.pages.map { it.id }).containsExactly(p1.id, moved.page.id).inOrder()
        assertThat(stored.pages.map { p -> p.shots.map { it.id } }).containsExactly(listOf("s1"), listOf("s2")).inOrder()
        assertThat(queue()).isEmpty()
    }

    @Test fun `a shot moved before it was uploaded lands on its new page`() = blocking {
        val a = album()
        shoot(a.id, "s1")
        shoot(a.id, "s2")
        shoot(a.id, "s3")
        repo.startNextPage(a.id)
        shoot(a.id, "s4")
        val moved = repo.moveRunToNextPage("s2") as MoveResult.Moved
        assertThat(processor.drain()).isEqualTo(UploadProcessor.Outcome.DONE)
        val log = requests()
        assertThat(log).contains("PUT albums/${a.id}/pages/${moved.page.id}/shots/s2")
        assertThat(log.indexOf("PUT albums/${a.id}/pages/${moved.page.id}/shots/s3"))
            .isLessThan(log.indexOf("POST albums/${a.id}/pages/${moved.page.id}/move"))
        val stored = fake.album(a.id)!!
        assertThat(stored.pages.map { p -> p.shots.map { it.id } }).containsExactly(listOf("s1"), listOf("s2", "s3", "s4")).inOrder()
    }

    @Test fun `a page emptied by a move is deleted on the server`() = blocking {
        val a = album()
        shoot(a.id, "s1")
        repo.startNextPage(a.id)
        val p2 = shoot(a.id, "s2").page
        val p3 = (repo.startNextPage(a.id) as NextPageResult.Started).page
        shoot(a.id, "s3")
        processor.drain()
        repo.moveRunToNextPage("s2")
        processor.drain()
        val stored = fake.album(a.id)!!
        assertThat(stored.pages.map { it.id }).doesNotContain(p2.id)
        assertThat(stored.pages.last().id).isEqualTo(p3.id)
        assertThat(stored.pages.last().shots.map { it.id }).containsExactly("s2", "s3").inOrder()
    }

    @Test fun `a move for an album gone from the server is dropped, other failures follow the usual rules`() = blocking {
        val a = album()
        shoot(a.id, "s1")
        processor.drain()
        fake.log.clear()
        env.db.ops().insert(UploadOp(kind = OpKind.MOVE_SHOTS, albumId = "gone", pageId = "p", shotId = "s1"))
        assertThat(processor.drain()).isEqualTo(UploadProcessor.Outcome.DONE)
        assertThat(queue()).isEmpty()
        env.db.ops().insert(UploadOp(kind = OpKind.MOVE_SHOTS, albumId = a.id, pageId = "p", shotId = "s1"))
        fake.failNext += 503
        assertThat(processor.drain()).isEqualTo(UploadProcessor.Outcome.RETRY)
        assertThat(queue()).hasSize(1)
    }

    @Test fun `409 and 422 are dropped and the queue moves on`() = blocking {
        val a = album()
        shoot(a.id, "s1")
        shoot(a.id, "s2")
        processor.drain()
        fake.log.clear()
        repeat(2) { env.db.ops().insert(UploadOp(kind = OpKind.ALBUM_META, albumId = a.id)) }
        fake.failNext += 409
        fake.failNext += 422
        assertThat(processor.drain()).isEqualTo(UploadProcessor.Outcome.DONE)
        assertThat(queue()).isEmpty()
        assertThat(fake.log).hasSize(2)
    }

    @Test fun `a shot that conflicts with a stored hash is dropped and kept on the phone`() = blocking {
        val a = album()
        val s = shoot(a.id, "s1")
        processor.drain()
        // Same id, different bytes: the server answers 409.
        File(s.shot.path).writeBytes(FakeAlbumServer.jpeg("different"))
        env.db.shots().delete("s1")
        env.db.shots().insert(s.shot.copy(sha256 = FakeAlbumServer.sha256(FakeAlbumServer.jpeg("different"))))
        env.db.ops().insert(UploadOp(kind = OpKind.PUT_SHOT, albumId = a.id, pageId = s.page.id, shotId = "s1"))
        assertThat(processor.drain()).isEqualTo(UploadProcessor.Outcome.DONE)
        assertThat(queue()).isEmpty()
        assertThat(File(s.shot.path).exists()).isTrue()
    }

    @Test fun `400 on a shot is retried once, then dropped with the shot kept`() = blocking {
        val a = album()
        val s = shoot(a.id, "s1")
        repeat(2) { processorStep() } // the two metadata ops
        fake.failNext += 400
        assertThat(processor.drain()).isEqualTo(UploadProcessor.Outcome.RETRY)
        assertThat(queue().single().attempts).isEqualTo(1)
        fake.failNext += 400
        assertThat(processor.drain()).isEqualTo(UploadProcessor.Outcome.DONE)
        assertThat(queue()).isEmpty()
        assertThat(File(s.shot.path).exists()).isTrue()
        assertThat(repo.shot("s1")).isNotNull()
    }

    @Test fun `400 on metadata is dropped`() = blocking {
        album()
        fake.failNext += 400
        assertThat(processor.drain()).isEqualTo(UploadProcessor.Outcome.DONE)
        assertThat(queue()).isEmpty()
    }

    @Test fun `413 is dropped with the shot kept on the phone`() = blocking {
        val a = album()
        shoot(a.id, "s1")
        repeat(2) { processorStep() }
        fake.failNext += 413
        assertThat(processor.drain()).isEqualTo(UploadProcessor.Outcome.DONE)
        assertThat(repo.shot("s1")).isNotNull()
        assertThat(fake.album(a.id)!!.pages.single().shots).isEmpty()
    }

    @Test fun `shot, page and album deletions are sent`() = blocking {
        val a = album()
        shoot(a.id, "s1")
        shoot(a.id, "s2")
        val p2 = (repo.startNextPage(a.id) as NextPageResult.Started).page
        shoot(a.id, "s3")
        repo.startNextPage(a.id)
        shoot(a.id, "s4")
        processor.drain()
        assertThat(fake.album(a.id)!!.pages).hasSize(3)

        repo.deleteShot("s1")
        repo.deletePage(p2.id)
        processor.drain()
        val stored = fake.album(a.id)!!
        assertThat(stored.pages).hasSize(2)
        assertThat(stored.pages.first().shots.map { it.id }).containsExactly("s2")
        assertThat(stored.pages.flatMap { p -> p.shots.map { it.id } }).doesNotContain("s3")

        repo.deleteAlbum(a.id, alsoOnServer = true)
        processor.drain()
        assertThat(fake.album(a.id)).isNull()
        assertThat(queue()).isEmpty()
    }

    @Test fun `an album deleted with its server copy never reappears from earlier queued uploads`() = blocking {
        val a = album()
        shoot(a.id, "s1")
        shoot(a.id, "s2")
        repo.deleteAlbum(a.id, alsoOnServer = true)
        assertThat(queue().map { it.kind }).containsExactly(OpKind.DELETE_ALBUM)
        processor.drain()
        assertThat(requests()).containsExactly("DELETE albums/${a.id}")
        assertThat(fake.albums).isEmpty()
    }

    @Test fun `a shot deleted before upload is not uploaded, but its deletion is sent`() = blocking {
        val a = album()
        shoot(a.id, "s1")
        shoot(a.id, "s2")
        repo.deleteShot("s1")
        processor.drain()
        assertThat(fake.log.none { it.startsWith("PUT") && it.endsWith("/shots/s1") }).isTrue()
        assertThat(fake.log.any { it.startsWith("DELETE") && it.endsWith("/shots/s1") }).isTrue()
        assertThat(fake.album(a.id)!!.pages.single().shots.map { it.id }).containsExactly("s2")
    }

    @Test fun `an op whose shot file vanished is dropped`() = blocking {
        val a = album()
        val s = shoot(a.id, "s1")
        File(s.shot.path).delete()
        assertThat(processor.drain()).isEqualTo(UploadProcessor.Outcome.DONE)
        assertThat(fake.log.none { it.endsWith("/shots/s1") }).isTrue()
    }

    @Test fun `ops queued while draining are picked up in the same run`() = blocking {
        val a = album()
        processor.moreQueued.set(true)
        shoot(a.id, "s1")
        assertThat(processor.drain()).isEqualTo(UploadProcessor.Outcome.DONE)
        assertThat(queue()).isEmpty()
    }

    @Test fun `the server albums list reflects what was uploaded`() = blocking {
        val a = album()
        shoot(a.id, "s1")
        repo.startNextPage(a.id)
        shoot(a.id, "s2")
        processor.drain()
        val list = (ServerAccess(env.settings).client()!!.listAlbums() as ApiResult.Ok).value
        assertThat(list.single()).isEqualTo(list.single().copy(id = a.id, name = "Family", pageSize = "letter", pages = 2, shots = 2))
    }

    /** Sends exactly the head op, by draining with every later op temporarily removed. */
    private suspend fun processorStep() {
        val all = queue()
        val rest = all.drop(1)
        rest.forEach { env.db.ops().delete(it.seq) }
        processor.drain()
        rest.forEach { env.db.ops().insert(it) }
    }
}
