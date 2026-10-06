package org.bit63.albumarchiver.ui.capture

import app.cash.turbine.test
import com.google.common.truth.Truth.assertThat
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.flow.first
import org.bit63.albumarchiver.MainDispatcherRule
import org.bit63.albumarchiver.TestEnv
import org.bit63.albumarchiver.TestViewModels
import org.bit63.albumarchiver.await
import org.bit63.albumarchiver.blocking
import org.bit63.albumarchiver.data.AddShotResult
import org.bit63.albumarchiver.data.Limits
import org.bit63.albumarchiver.data.OpKind
import org.bit63.albumarchiver.data.Page
import org.bit63.albumarchiver.data.PageSize
import org.bit63.albumarchiver.testing.FakeCamera
import org.junit.After
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import java.io.File
import java.io.IOException
import kotlin.time.Duration.Companion.seconds

@RunWith(RobolectricTestRunner::class)
class CaptureViewModelTest {
    @get:Rule val main = MainDispatcherRule()

    private val env = TestEnv()
    private val camera = FakeCamera()
    private val vms = TestViewModels()
    private val vm by lazy { vms.create { CaptureViewModel(env.repo, env.settings, env.store, camera) } }

    @After fun tearDown() {
        vms.clear()
        env.close()
    }

    private suspend fun openAlbum(): String {
        val a = env.repo.createAlbum("Family", PageSize.DEFAULT)
        env.settings.setLastAlbumId(a.id)
        vm.state.await { it.album?.id == a.id }
        return a.id
    }

    private suspend fun shotsSaved(n: Int) = vm.state.await { it.shotCount == n && it.shots.size == n }

    /** The next event of type [E], skipping others (for example the ShotSaved of an earlier shot). */
    private suspend inline fun <reified E : CaptureEvent> app.cash.turbine.ReceiveTurbine<CaptureEvent>.next(): E {
        while (true) {
            val e = awaitItem()
            if (e is E) return e
        }
    }

    @Test fun `with no last album the empty state shows and no shot is taken`() = blocking {
        val s = vm.state.await { it.loaded }
        assertThat(s.album).isNull()
        vm.takeShot()
        vm.nextPage()
        Thread.sleep(200)
        assertThat(camera.captures.get()).isEqualTo(0)
    }

    @Test fun `a last album that was deleted shows the empty state`() = blocking {
        env.settings.setLastAlbumId("gone")
        val s = vm.state.await { it.loaded }
        assertThat(s.album).isNull()
    }

    @Test fun `opening an album makes its last page current`() = blocking {
        val a = env.repo.createAlbum("Family", PageSize.DEFAULT)
        env.repo.addShot(a.id, "s1", env.written("s1"))
        env.repo.startNextPage(a.id)
        env.repo.addShot(a.id, "s2", env.written("s2"))
        env.settings.setLastAlbumId(a.id)
        val s = vm.state.await { it.album != null && it.shots.isNotEmpty() }
        assertThat(s.pageNumber).isEqualTo(2)
        assertThat(s.shots.map { it.id }).containsExactly("s2")
    }

    @Test fun `the shutter adds a shot to page 1 of a new album`() = blocking {
        val albumId = openAlbum()
        vm.events.test(timeout = 5.seconds) {
            vm.takeShot()
            val saved = next<CaptureEvent.ShotSaved>()
            assertThat(File(saved.shot.path).isFile).isTrue()
        }
        val s = shotsSaved(1)
        assertThat(s.pageNumber).isEqualTo(1)
        assertThat(s.shots).hasSize(1)
        assertThat(env.repo.pages(albumId)).hasSize(1)
        assertThat(env.db.ops().all().map { it.kind }).contains(OpKind.PUT_SHOT)
    }

    @Test fun `thumbnails are listed most recent last`() = blocking {
        openAlbum()
        repeat(3) {
            vm.takeShot()
            shotsSaved(it + 1)
        }
        val s = vm.state.await { it.shots.size == 3 }
        assertThat(s.shots.map { it.takenAt }).isInOrder()
    }

    @Test fun `presses while a capture is in flight are dropped, not queued`() = blocking {
        openAlbum()
        val gate = CompletableDeferred<Unit>()
        camera.gate = gate
        vm.takeShot()
        vm.capturing.await { it }
        repeat(5) { vm.takeShot() }
        gate.complete(Unit)
        shotsSaved(1)
        vm.capturing.await { !it }
        Thread.sleep(200)
        assertThat(camera.captures.get()).isEqualTo(1)
        assertThat(vm.state.value.shotCount).isEqualTo(1)
    }

    @Test fun `a camera failure shows an error and adds no shot`() = blocking {
        val albumId = openAlbum()
        camera.fail = IOException("lens cap")
        vm.events.test(timeout = 5.seconds) {
            vm.takeShot()
            val m = next<CaptureEvent.Message>()
            assertThat(m.text).contains("lens cap")
        }
        assertThat(env.repo.pages(albumId)).isEmpty()
        assertThat(env.store.root.walk().filter { it.isFile }.toList()).isEmpty()
    }

    @Test fun `next page on a page with shots starts the next page`() = blocking {
        openAlbum()
        vm.takeShot()
        shotsSaved(1)
        vm.events.test(timeout = 5.seconds) {
            vm.nextPage()
            val e = next<CaptureEvent.PageStarted>()
            assertThat(e.number).isEqualTo(2)
        }
        val s = vm.state.await { it.pageNumber == 2 && it.shots.isEmpty() }
        assertThat(s.shotCount).isEqualTo(0)
    }

    @Test fun `next page on an empty page is refused with a hint`() = blocking {
        val albumId = openAlbum()
        vm.events.test(timeout = 5.seconds) {
            vm.nextPage()
            assertThat((next<CaptureEvent.Message>()).text).contains("no shots yet")
        }
        assertThat(env.repo.pages(albumId)).isEmpty()
    }

    @Test fun `undo removes the new page`() = blocking {
        val albumId = openAlbum()
        vm.takeShot()
        shotsSaved(1)
        var pageId = ""
        vm.events.test(timeout = 5.seconds) {
            vm.nextPage()
            pageId = (next<CaptureEvent.PageStarted>()).pageId
        }
        vm.state.await { it.pageNumber == 2 }
        vm.undoNextPage(pageId)
        val s = vm.state.await { it.pageNumber == 1 && it.shotCount == 1 }
        assertThat(s.page!!.id).isNotEqualTo(pageId)
        assertThat(env.repo.pages(albumId)).hasSize(1)
    }

    @Test fun `the 26th shot is refused and the page is reported full`() = blocking {
        val albumId = openAlbum()
        repeat(Limits.MAX_SHOTS_PER_PAGE) { i ->
            assertThat(env.repo.addShot(albumId, "s$i", env.written("s$i"))).isInstanceOf(AddShotResult.Added::class.java)
        }
        val s = vm.state.await { it.shotCount == 25 }
        assertThat(s.pageFull).isTrue()
        vm.events.test(timeout = 5.seconds) {
            vm.takeShot()
            assertThat((next<CaptureEvent.Message>()).text).isEqualTo(CaptureViewModel.PAGE_FULL)
        }
        assertThat(camera.captures.get()).isEqualTo(0)
        assertThat(env.repo.lastPage(albumId)!!.shotCount).isEqualTo(25)
    }

    @Test fun `the 501st page is refused and the album is reported full`() = blocking {
        val albumId = openAlbum()
        env.db.pages().insertAll((1..Limits.MAX_PAGES_PER_ALBUM).map { Page("p$it", albumId, it, shotCount = 1) })
        val s = vm.state.await { it.pageNumber == 500 }
        assertThat(s.albumFull).isTrue()
        vm.events.test(timeout = 5.seconds) {
            vm.nextPage()
            assertThat((next<CaptureEvent.Message>()).text).isEqualTo(CaptureViewModel.ALBUM_FULL)
        }
        assertThat(env.repo.pages(albumId)).hasSize(500)
    }

    @Test fun `flash is off by default and toggles the camera`() = blocking {
        assertThat(vm.flashOn.value).isFalse()
        vm.toggleFlash()
        assertThat(vm.flashOn.value).isTrue()
        assertThat(camera.flashOn).isTrue()
        vm.toggleFlash()
        assertThat(camera.flashOn).isFalse()
    }

    @Test fun `low storage is reported below 500 MB and shots are still allowed`() = blocking {
        env.freeBytes = 100L * 1024 * 1024
        vm.refreshStorage()
        vm.lowStorage.await { it }
        openAlbum()
        vm.takeShot()
        shotsSaved(1)
    }

    @Test fun `pending uploads and server banners`() = blocking {
        openAlbum()
        var s = vm.state.await { it.loaded }
        assertThat(s.serverConfigured).isFalse()
        vm.takeShot()
        s = vm.state.await { it.pendingUploads == 1 }
        env.settings.setServer("http://server", "t")
        s = vm.state.await { it.serverConfigured }
        assertThat(s.authRejected).isFalse()
        env.settings.setAuthRejected(true)
        vm.state.await { it.authRejected }
    }

    @Test fun `switching albums follows the last album setting`() = blocking {
        openAlbum()
        val other = env.repo.createAlbum("Other", PageSize.Preset.A4)
        env.settings.setLastAlbumId(other.id)
        val s = vm.state.await { it.album?.id == other.id }
        assertThat(s.album!!.name).isEqualTo("Other")
        assertThat(env.settings.lastAlbumId.first()).isEqualTo(other.id)
    }
}
