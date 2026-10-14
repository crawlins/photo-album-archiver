package org.bit63.albumarchiver.ui.page

import com.google.common.truth.Truth.assertThat
import org.bit63.albumarchiver.MainDispatcherRule
import org.bit63.albumarchiver.TestEnv
import org.bit63.albumarchiver.TestViewModels
import org.bit63.albumarchiver.await
import org.bit63.albumarchiver.blocking
import org.bit63.albumarchiver.data.NextPageResult
import org.bit63.albumarchiver.data.PageFeedback
import org.bit63.albumarchiver.data.PageSize
import org.bit63.albumarchiver.data.ShotRect
import org.bit63.albumarchiver.testing.FakeAlbumServer
import org.bit63.albumarchiver.upload.PreviewFetcher
import org.bit63.albumarchiver.upload.ServerAccess
import org.junit.After
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import java.nio.file.Files

@RunWith(RobolectricTestRunner::class)
class PageViewModelTest {
    @get:Rule val main = MainDispatcherRule()
    private val fake = FakeAlbumServer().start()
    private val env = TestEnv(fake.url, fake.token)
    private val previews = PreviewFetcher(Files.createTempDirectory("previews").toFile(), ServerAccess(env.settings))
    private val vms = TestViewModels()
    private lateinit var albumId: String
    private lateinit var pageId: String

    @After fun tearDown() {
        vms.clear()
        fake.shutdown()
        env.close()
    }

    private val square = listOf(PagePoint(0f, 0f), PagePoint(1f, 0f), PagePoint(1f, 1f), PagePoint(0f, 1f))

    @Test fun `point in polygon and the smallest outline under a tap`() {
        assertThat(contains(square, PagePoint(0.5f, 0.5f))).isTrue()
        assertThat(contains(square, PagePoint(1.5f, 0.5f))).isFalse()
        val tri = listOf(PagePoint(0f, 0f), PagePoint(1f, 0f), PagePoint(0f, 1f))
        assertThat(contains(tri, PagePoint(0.2f, 0.2f))).isTrue()
        assertThat(contains(tri, PagePoint(0.8f, 0.8f))).isFalse()
        assertThat(contains(listOf(PagePoint(0f, 0f), PagePoint(1f, 1f)), PagePoint(0.5f, 0.5f))).isFalse()
        val big = Outline("big", 1, square)
        val small = Outline("small", 2, listOf(PagePoint(0.4f, 0.4f), PagePoint(0.6f, 0.4f), PagePoint(0.6f, 0.6f), PagePoint(0.4f, 0.6f)))
        assertThat(outlineAt(listOf(big, small), PagePoint(0.5f, 0.5f))).isEqualTo(small)
        assertThat(outlineAt(listOf(big, small), PagePoint(0.1f, 0.1f))).isEqualTo(big)
        assertThat(outlineAt(listOf(small), PagePoint(0.1f, 0.1f))).isNull()
        val r = ShotRect(0.6f, 0f, 1f, 1f, "single_coverage")
        assertThat(rectAt(listOf(r), PagePoint(0.8f, 0.5f))).isEqualTo(r)
        assertThat(rectAt(listOf(r), PagePoint(0.2f, 0.5f))).isNull()
    }

    private suspend fun album(uploaded: Boolean = true) {
        albumId = env.repo.createAlbum("A", PageSize.DEFAULT).id
        env.repo.addShot(albumId, "s1", env.written("s1"))
        env.repo.addShot(albumId, "s2", env.written("s2"))
        pageId = env.repo.lastPage(albumId)!!.id
        if (uploaded) env.db.ops().all().forEach { env.db.ops().delete(it.seq) }
    }

    private val feedbackJson = """[{"code":"single_coverage","fraction":0.31,"locations":["right"],
        "regions":[{"bbox":[0.62,0,1,1],"location":"right","shots":[1],"shot_boxes":{"s2":[10,0,99,99]}}],
        "message":"Only one photo covers the right edge (31.0% of the page)."}]"""
    private val outlinesJson = """{"s1":[[0,0],[0.65,0],[0.65,1],[0,1]],"s2":[[0.35,0],[1,0],[1,1],[0.35,1]],"gone":[[0,0],[1,0],[1,1]]}"""

    private fun feedback(preview: Boolean = true, lastRun: String? = "run1") =
        PageFeedback(pageId, albumId, "done", lastRun, feedbackJson, env.clock.instant(), preview = preview, outlinesJson = outlinesJson)

    private fun pageView() = vms.create { PageViewModel(env.repo, previews, albumId, pageId) }

    @Test fun `outlines are numbered as the strip and only for shots of the page, rectangles from the regions`() = blocking {
        album()
        env.repo.saveFeedback(listOf(feedback()))
        val vm = pageView()
        val s = vm.state.await { it.outlines.size == 2 }
        assertThat(s.outlines.map { it.shotId to it.number }).containsExactly("s1" to 1, "s2" to 2).inOrder()
        assertThat(s.outlines[1].points.first()).isEqualTo(PagePoint(0.35f, 0f))
        assertThat(s.rects.single()).isEqualTo(ShotRect(0.62f, 0f, 1f, 1f, "single_coverage"))
        assertThat(s.hasPreview).isTrue()
        assertThat(s.warnings.single().code).isEqualTo("single_coverage")
    }

    @Test fun `nothing is drawn while the page's shots are still uploading`() = blocking {
        album(uploaded = false)
        env.repo.saveFeedback(listOf(feedback()))
        val vm = pageView()
        vm.state.await { it.feedback != null && it.shots.size == 2 }
        Thread.sleep(200)
        assertThat(vm.state.value.outlines).isEmpty()
        assertThat(vm.state.value.warnings).isEmpty()
    }

    @Test fun `a tap selects an outline, a second opens it, a rectangle opens the shot showing it`() = blocking {
        album()
        env.repo.saveFeedback(listOf(feedback()))
        val vm = pageView()
        vm.state.await { it.outlines.size == 2 }
        assertThat(vm.tap(PagePoint(0.2f, 0.5f))).isNull()  // inside s1 only: selects it
        assertThat(vm.state.await { it.selectedShotId == "s1" }.selectedShotId).isEqualTo("s1")
        assertThat(vm.tap(PagePoint(0.2f, 0.5f))).isEqualTo(0)  // selected already: opens shot 1
        vm.select(null)
        vm.state.await { it.selectedShotId == null }
        assertThat(vm.tap(PagePoint(0.5f, 0.5f))).isNull()  // in both outlines: the smaller (equal here, s1 first) is selected
        assertThat(vm.tap(PagePoint(0.8f, 0.5f))).isEqualTo(1)  // in the single-coverage rectangle, shown by s2
        vm.select(null)
        assertThat(vm.state.await { it.selectedShotId == null }.shotToOpen()).isEqualTo(1)  // the first shot that shows a warning
    }

    @Test fun `the preview is fetched once per run and again when the run changes`() = blocking {
        album()
        fake.seed(albumId, "A", "5x4in", emptyList())
        fake.previews[pageId] = FakeAlbumServer.jpeg("preview-1")
        env.repo.saveFeedback(listOf(feedback()))
        val vm = pageView()
        val first = vm.preview.await { it != null }!!
        assertThat(first.readBytes()).isEqualTo(FakeAlbumServer.jpeg("preview-1"))
        val before = fake.log.count { it.endsWith("/preview") }
        env.repo.saveFeedback(listOf(feedback(lastRun = "run1")))  // same run: no fetch
        Thread.sleep(200)
        assertThat(fake.log.count { it.endsWith("/preview") }).isEqualTo(before)
        fake.previews[pageId] = FakeAlbumServer.jpeg("preview-2")
        env.repo.saveFeedback(listOf(feedback(lastRun = "run2")))
        vm.preview.await { it != null && it.readBytes().contentEquals(FakeAlbumServer.jpeg("preview-2")) }
        env.repo.saveFeedback(listOf(feedback(preview = false, lastRun = "run3")))
        vm.preview.await { it == null }
    }

    @Test fun `add shots to this page makes it the target page`() = blocking {
        album()
        env.repo.startNextPage(albumId).let { assertThat(it).isInstanceOf(NextPageResult.Started::class.java) }
        val vm = pageView()
        vm.addShotsToPage()
        env.repo.observeTargetPage(albumId).await { it?.id == pageId }
    }
}
