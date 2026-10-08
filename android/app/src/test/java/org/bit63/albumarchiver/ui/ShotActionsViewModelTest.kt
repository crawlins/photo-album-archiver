package org.bit63.albumarchiver.ui

import com.google.common.truth.Truth.assertThat
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withTimeout
import org.bit63.albumarchiver.MainDispatcherRule
import org.bit63.albumarchiver.TestEnv
import org.bit63.albumarchiver.TestViewModels
import org.bit63.albumarchiver.await
import org.bit63.albumarchiver.blocking
import org.bit63.albumarchiver.data.NextPageResult
import org.bit63.albumarchiver.data.PageSize
import org.bit63.albumarchiver.testing.FakeAlbumServer
import org.bit63.albumarchiver.testing.FakeCamera
import org.bit63.albumarchiver.ui.capture.CaptureEvent
import org.bit63.albumarchiver.ui.capture.CaptureViewModel
import org.bit63.albumarchiver.ui.review.ReviewViewModel
import org.bit63.albumarchiver.ui.review.Slot
import org.bit63.albumarchiver.upload.PageLoader
import org.bit63.albumarchiver.upload.ServerAccess
import org.bit63.albumarchiver.upload.ShotFetcher
import org.junit.After
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

/** Selecting shots and carrying out the actions from the capture and review screens. */
@RunWith(RobolectricTestRunner::class)
class ShotActionsViewModelTest {
    @get:Rule val main = MainDispatcherRule()
    private val fake = FakeAlbumServer().start()
    private val env = TestEnv(fake.url, fake.token)
    private val server = ServerAccess(env.settings)
    private val vms = TestViewModels()
    private lateinit var albumId: String
    private val pageIds = mutableListOf<String>()

    @After fun tearDown() {
        vms.clear()
        fake.shutdown()
        env.close()
    }

    /** An album with pages of the given shot counts, shot ids "p<page>s<n>", opened as the last album. */
    private suspend fun album(vararg counts: Int) {
        albumId = env.repo.createAlbum("A", PageSize.DEFAULT).id
        counts.forEachIndexed { i, n ->
            if (i > 0) pageIds += (env.repo.startNextPage(albumId) as NextPageResult.Started).page.id
            repeat(n) { j -> env.repo.addShot(albumId, "p${i + 1}s${j + 1}", env.written("p${i + 1}s${j + 1}")) }
            if (i == 0) pageIds += env.repo.lastPage(albumId)!!.id
        }
        env.settings.setLastAlbumId(albumId)
    }

    private fun capture() = vms.create { CaptureViewModel(env.repo, env.settings, env.store, FakeCamera()) }

    private fun review(pageIndex: Int, shot: Int = 0) = vms.create {
        ReviewViewModel(env.repo, PageLoader(env.repo, server), ShotFetcher(env.repo, env.store, server), albumId, pageIds[pageIndex], shot)
    }

    private suspend fun CaptureViewModel.ready(shots: Int) = state.await { it.album != null && it.shots.size == shots }

    private suspend fun ShotActions.openMenuFor(id: String): ShotActionsMenu {
        onLongPress(id)
        assertThat(selected.value).isEqualTo(id)
        onLongPress(id)
        return menu.await { it != null }!!
    }

    // ---- Selection ----

    @Test fun `a long press selects, a second one opens the menu, and dismissing keeps the selection`() = blocking {
        album(3)
        val vm = capture()
        vm.ready(3)
        val actions = vm.shotActions
        actions.onLongPress("p1s2")
        assertThat(actions.selected.value).isEqualTo("p1s2")
        assertThat(actions.menu.value).isNull()
        val menu = actions.openMenuFor("p1s2")
        assertThat(menu.shot.id).isEqualTo("p1s2")
        assertThat(menu.title).isEqualTo("Page 1, photo 2 of 3")
        actions.closeMenu()
        assertThat(actions.menu.value).isNull()
        assertThat(actions.selected.value).isEqualTo("p1s2")
    }

    @Test fun `taps reselect or clear while a shot is selected and are not consumed otherwise`() = blocking {
        album(3)
        val actions = capture().also { it.ready(3) }.shotActions
        assertThat(actions.onTap("p1s1")).isFalse()
        actions.onLongPress("p1s1")
        assertThat(actions.onTap("p1s3")).isTrue()
        assertThat(actions.selected.value).isEqualTo("p1s3")
        // a long press on another thumbnail selects it instead of opening the menu
        actions.onLongPress("p1s2")
        assertThat(actions.selected.value).isEqualTo("p1s2")
        assertThat(actions.menu.value).isNull()
        assertThat(actions.onTap("p1s2")).isTrue()
        assertThat(actions.selected.value).isNull()
    }

    @Test fun `taking a shot or starting the next page clears the selection`() = blocking {
        album(2)
        val vm = capture()
        vm.ready(2)
        vm.shotActions.onLongPress("p1s1")
        vm.takeShot()
        assertThat(vm.shotActions.selected.value).isNull()
        vm.ready(3)
        vm.shotActions.onLongPress("p1s1")
        vm.nextPage()
        assertThat(vm.shotActions.selected.value).isNull()
    }

    @Test fun `the selection clears when the strip shows another page`() = blocking {
        album(2)
        val vm = capture()
        vm.ready(2)
        vm.shotActions.onLongPress("p1s1")
        env.repo.startNextPage(albumId)
        vm.shotActions.selected.await { it == null }
    }

    // ---- Capture screen ----

    @Test fun `moving the end of the last page makes a new current page with those shots`() = blocking {
        album(4)
        val vm = capture()
        vm.ready(4)
        val menu = vm.shotActions.openMenuFor("p1s3")
        assertThat(menu.moveLabel).isEqualTo("Move this photo and the 1 after it to a new page 2")
        vm.shotAction(ShotAction.MOVE_TO_NEXT_PAGE)
        // The source page is left with 2 shots as well, so wait for the moved ones, not a count.
        val s = vm.state.await { it.pageNumber == 2 && it.shots.map { shot -> shot.id } == listOf("p1s3", "p1s4") }
        assertThat(s.page?.id).isNotEqualTo(pageIds[0])
        val event = withTimeout(5000) { vm.events.first { it is CaptureEvent.Message } } as CaptureEvent.Message
        assertThat(event.text).isEqualTo("Moved 2 photos to page 2")
        assertThat(vm.shotActions.selected.value).isNull()
    }

    @Test fun `deleting the run from the capture screen leaves the earlier shots`() = blocking {
        album(4)
        val vm = capture()
        vm.ready(4)
        vm.shotActions.openMenuFor("p1s2")
        vm.shotAction(ShotAction.DELETE_RUN)
        val s = vm.ready(1)
        assertThat(s.shots.map { it.id }).containsExactly("p1s1")
    }

    @Test fun `a refused action changes nothing and says why`() = blocking {
        album(2, 3)
        val vm = capture()
        vm.ready(3)
        vm.shotActions.openMenuFor("p2s1")
        vm.shotAction(ShotAction.NEW_PAGE)
        val event = withTimeout(5000) { vm.events.first { it is CaptureEvent.Message } } as CaptureEvent.Message
        assertThat(event.text).isEqualTo(ShotActionsMenu.WHOLE_PAGE)
        assertThat(env.repo.pages(albumId)).hasSize(2)
    }

    // ---- Review screen ----

    @Test fun `splitting from the review screen shows the first moved shot on its new page`() = blocking {
        album(4, 1)
        val vm = review(0, 2)
        vm.shots.await { it.size == 4 }
        vm.shotActions.openMenuFor("p1s3")
        vm.shotAction(ShotAction.NEW_PAGE)
        val pos = vm.position.await { it?.pageId != pageIds[0] }!!
        val newPage = env.repo.pages(albumId)[1]
        assertThat(pos).isEqualTo(Slot(newPage.id, 0))
        // The source page is left with 2 shots as well, so wait for the moved ones, not a count.
        vm.shots.await { it.map { shot -> shot.id } == listOf("p1s3", "p1s4") }
        assertThat(env.repo.pages(albumId).map { it.position to it.shotCount }).containsExactly(1 to 2, 2 to 2, 3 to 1).inOrder()
        assertThat(withTimeout(5000) { vm.messages.first() }).isEqualTo("Made page 2 from 2 photos")
    }

    @Test fun `moving into the next page from the review screen lands on the first moved shot there`() = blocking {
        album(3, 2)
        val vm = review(0)
        vm.shots.await { it.size == 3 }
        vm.shotActions.openMenuFor("p1s2")
        vm.shotAction(ShotAction.MOVE_TO_NEXT_PAGE)
        assertThat(vm.position.await { it?.pageId == pageIds[1] }).isEqualTo(Slot(pageIds[1], 0))
        assertThat(vm.shots.await { it.size == 4 }.map { it.id }).containsExactly("p1s2", "p1s3", "p2s1", "p2s2").inOrder()
    }

    @Test fun `deleting a whole inner page's run from review lands on the next shot`() = blocking {
        album(1, 2, 1)
        val vm = review(1)
        vm.shots.await { it.size == 2 }
        vm.shotActions.openMenuFor("p2s1")
        vm.shotAction(ShotAction.DELETE_RUN)
        assertThat(vm.position.await { it?.pageId == pageIds[2] }).isEqualTo(Slot(pageIds[2], 0))
        assertThat(env.repo.pages(albumId)).hasSize(2)
    }

    @Test fun `the page arrows clear the selection`() = blocking {
        album(1, 1)
        val vm = review(0)
        vm.pages.await { it.size == 2 }
        vm.shotActions.onLongPress("p1s1")
        vm.showPage(pageIds[1])
        assertThat(vm.shotActions.selected.value).isNull()
    }
}
