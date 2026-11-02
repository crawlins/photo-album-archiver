package org.bit63.albumarchiver.upload

import com.google.common.truth.Truth.assertThat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.flowOf
import org.bit63.albumarchiver.TestEnv
import org.bit63.albumarchiver.await
import org.bit63.albumarchiver.blocking
import org.bit63.albumarchiver.data.PageFeedback
import org.bit63.albumarchiver.data.PageSize
import org.bit63.albumarchiver.testing.FakeAlbumServer
import org.junit.After
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import java.time.Instant

@RunWith(RobolectricTestRunner::class)
class FeedbackPollerTest {
    private val fake = FakeAlbumServer().start()
    private val env = TestEnv(fake.url, fake.token)
    private val poller = FeedbackPoller(env.repo, ServerAccess(env.settings), env.clock, periodMs = 200)

    @After fun tearDown() {
        fake.shutdown()
        env.close()
    }

    /** A local album of two pages, known to the server, whose status says page 1 has single coverage. */
    private suspend fun album(): Triple<String, String, String> {
        val a = env.repo.createAlbum("A", PageSize.DEFAULT)
        env.repo.addShot(a.id, "s1", env.written("s1"))
        val p1 = env.repo.lastPage(a.id)!!.id
        env.repo.startNextPage(a.id)
        val p2 = env.repo.lastPage(a.id)!!.id
        fake.seed(a.id, "A", "5x4in", emptyList())
        fake.statusPages[a.id] = """[
            {"id":"$p1","number":1,"state":"done","last_run":"2026-10-14T01:00:00Z","warnings":[
              {"code":"single_coverage","fraction":0.31,"locations":["right"],
               "regions":[{"bbox":[0.62,0,1,1],"location":"right","shots":[0],"shot_boxes":{"s1":[2210,0,4079,3071]}}],
               "message":"Only one photo covers the right edge (31.0% of the page)."}]},
            {"id":"$p2","number":2,"state":"empty","last_run":null,"warnings":[]},
            {"id":"not-on-this-phone","number":3,"state":"done","last_run":null,"warnings":[]}
        ]"""
        return Triple(a.id, p1, p2)
    }

    @Test fun `refresh stores each known page's state and warnings`() = blocking {
        val (albumId, p1, p2) = album()
        assertThat(poller.refresh(albumId)).isTrue()
        val rows = env.repo.observeFeedback(albumId).first().associateBy { it.pageId }
        assertThat(rows.keys).containsExactly(p1, p2)
        assertThat(rows[p1]!!.state).isEqualTo("done")
        assertThat(rows[p1]!!.lastRun).isEqualTo("2026-10-14T01:00:00Z")
        assertThat(rows[p1]!!.warnings.single().regions.single().shotBoxes).containsKey("s1")
        assertThat(rows[p2]!!.warnings).isEmpty()
    }

    @Test fun `warned pages leave out pages with shots still to upload`() = blocking {
        val (albumId, p1, _) = album()
        poller.refresh(albumId)
        assertThat(env.repo.observeWarnedPages(albumId).first()).isEmpty()  // s1 is still queued
        env.db.ops().all().forEach { env.db.ops().delete(it.seq) }
        assertThat(env.repo.observeWarnedPages(albumId).await { it.isNotEmpty() }).containsExactly(p1)
    }

    private fun status(albumId: String, p1: String, p2: String, state: String, warned: Boolean) {
        val warnings = if (!warned) "[]" else """[{"code":"single_coverage","locations":["right"],
            "regions":[{"bbox":[0.62,0,1,1],"location":"right","shots":[0],"shot_boxes":{"s1":[2210,0,4079,3071]}}],
            "message":"Only one photo covers the right edge."}]"""
        fake.statusPages[albumId] = """[
            {"id":"$p1","number":1,"state":"$state","last_run":"2026-10-14T01:00:00Z","warnings":$warnings},
            {"id":"$p2","number":2,"state":"empty","last_run":null,"warnings":[]}
        ]"""
    }

    @Test fun `a page's old warnings stay hidden after new shots upload until the server has run it again`() = blocking {
        val (albumId, p1, p2) = album()
        val processor = UploadProcessor(env.db, env.settings, ServerAccess(env.settings), env.clock)
        processor.drain()
        poller.refresh(albumId)
        assertThat(env.repo.observeWarnedPages(albumId).first()).containsExactly(p1)

        // The user adds a shot to page 1 to cover the area.
        env.repo.setTargetPage(albumId, p1)
        env.repo.addShot(albumId, "s1b", env.written("s1b"))
        assertThat(env.repo.observeWarnedPages(albumId).first()).isEmpty()  // queued

        // Uploaded, but the status still holds the old run: no warning yet.
        processor.drain()
        assertThat(env.db.ops().all()).isEmpty()
        assertThat(env.repo.observeWarnedPages(albumId).first()).isEmpty()
        assertThat(env.repo.observeWarnedCounts().first()).isEmpty()

        // The server reports it waiting, then processing, still with the old warnings.
        status(albumId, p1, p2, "changed", warned = true)
        poller.refresh(albumId)
        assertThat(env.repo.observeWarnedPages(albumId).first()).isEmpty()
        status(albumId, p1, p2, "processing", warned = true)
        poller.refresh(albumId)
        assertThat(env.repo.observeWarnedPages(albumId).first()).isEmpty()

        // The run with the new shot is done: its own result is shown.
        status(albumId, p1, p2, "done", warned = false)
        poller.refresh(albumId)
        assertThat(env.repo.observeWarnedPages(albumId).first()).isEmpty()
        status(albumId, p1, p2, "done", warned = true)
        poller.refresh(albumId)
        assertThat(env.repo.observeWarnedPages(albumId).first()).containsExactly(p1)
    }

    @Test fun `a status asked for before a shot finished uploading does not bring old warnings back`() = blocking {
        val (albumId, p1, _) = album()
        env.db.ops().all().forEach { env.db.ops().delete(it.seq) }
        val warning = """[{"code":"glare","message":"m","regions":[]}]"""
        fun row(at: Long) = PageFeedback(p1, albumId, "done", "run1", warning, Instant.ofEpochSecond(at))
        env.repo.saveFeedback(listOf(row(100)))
        assertThat(env.repo.observeWarnedPages(albumId).first()).containsExactly(p1)
        env.db.feedback().markChanged(p1, Instant.ofEpochSecond(200))
        assertThat(env.repo.observeWarnedPages(albumId).first()).isEmpty()
        env.repo.saveFeedback(listOf(row(150)))  // asked before the upload ended, answered after
        assertThat(env.db.feedback().get(p1)!!.state).isEqualTo("changed")
        assertThat(env.repo.observeWarnedPages(albumId).first()).isEmpty()
        env.repo.saveFeedback(listOf(row(250)))  // asked afterwards: the server's word stands
        assertThat(env.repo.observeWarnedPages(albumId).first()).containsExactly(p1)
    }

    @Test fun `no server or a failed request stores nothing and reports false`() = blocking {
        val (albumId, _, _) = album()
        fake.failNext += 500
        assertThat(poller.refresh(albumId)).isFalse()
        assertThat(env.repo.observeFeedback(albumId).first()).isEmpty()
        val offline = TestEnv()
        try {
            assertThat(FeedbackPoller(offline.repo, ServerAccess(offline.settings)).refresh(albumId)).isFalse()
        } finally {
            offline.close()
        }
    }

    @Test fun `start polls at once, on the timer and when an upload run ends, until cancelled`() = blocking {
        val (albumId, _, _) = album()
        val running = MutableStateFlow(false)
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
        poller.start(scope, flowOf(albumId), running)
        env.repo.observeFeedback(albumId).await { it.isNotEmpty() }
        val afterFirst = fake.statusRequests
        assertThat(afterFirst).isAtLeast(1)
        Thread.sleep(500)
        assertThat(fake.statusRequests).isAtLeast(afterFirst + 2)  // the 200 ms timer
        val slow = FeedbackPoller(env.repo, ServerAccess(env.settings), env.clock, periodMs = 60_000)
        scope.cancel()
        val scope2 = CoroutineScope(SupervisorJob() + Dispatchers.Default)
        slow.start(scope2, flowOf(albumId), running)
        Thread.sleep(300)
        val before = fake.statusRequests
        running.value = true
        Thread.sleep(200)
        assertThat(fake.statusRequests).isEqualTo(before)  // starting an upload is not a trigger
        running.value = false
        Thread.sleep(500)
        assertThat(fake.statusRequests).isEqualTo(before + 1)  // its end is
        scope2.cancel()
        Thread.sleep(300)
        val stopped = fake.statusRequests
        running.value = true
        running.value = false
        Thread.sleep(300)
        assertThat(fake.statusRequests).isEqualTo(stopped)
    }
}
