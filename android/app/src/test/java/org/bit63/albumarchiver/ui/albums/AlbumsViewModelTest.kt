package org.bit63.albumarchiver.ui.albums

import com.google.common.truth.Truth.assertThat
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.first
import org.bit63.albumarchiver.MainDispatcherRule
import org.bit63.albumarchiver.TestEnv
import org.bit63.albumarchiver.TestViewModels
import org.bit63.albumarchiver.await
import org.bit63.albumarchiver.blocking
import org.bit63.albumarchiver.data.OpKind
import org.bit63.albumarchiver.data.PageSize
import org.junit.After
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import java.time.LocalDate

@RunWith(RobolectricTestRunner::class)
class AlbumsViewModelTest {
    @get:Rule val main = MainDispatcherRule()
    private val env = TestEnv()
    private val running = MutableStateFlow(false)
    private val vms = TestViewModels()
    private val vm by lazy { vms.create { AlbumsViewModel(env.repo, env.settings, running) } }

    @After fun tearDown() {
        vms.clear()
        env.close()
    }

    @Test fun `creating an album makes it the current album`() = blocking {
        vm.create(AlbumForm.forNew(LocalDate.of(2026, 10, 4)))
        val id = env.settings.lastAlbumId.await { it != null }!!
        val album = env.repo.album(id)!!
        assertThat(album.name).isEqualTo("Album 2026-10-04")
        assertThat(album.pageSize).isEqualTo("letter")
        val rows = vm.albums.await { it.isNotEmpty() }
        assertThat(rows.single().summary.name).isEqualTo("Album 2026-10-04")
        assertThat(rows.single().summary.pageCount).isEqualTo(0)
    }

    @Test fun `an invalid form saves nothing`() = blocking {
        vm.create(AlbumForm(name = " "))
        vm.create(AlbumForm(name = "x", choice = SizeChoice.CUSTOM, width = "0", height = "1"))
        Thread.sleep(200)
        assertThat(env.repo.observeSummaries().first()).isEmpty()
        assertThat(env.settings.lastAlbumId.first()).isNull()
    }

    @Test fun `editing name and size queues metadata and leaves shots alone`() = blocking {
        val a = env.repo.createAlbum("Old", PageSize.DEFAULT)
        env.repo.addShot(a.id, "s1", env.written("s1"))
        vm.update(a.id, AlbumForm("New", SizeChoice.CUSTOM, "254", "305", PageSize.Unit.MM))
        env.repo.observeAlbum(a.id).await { it?.name == "New" }
        assertThat(env.repo.album(a.id)!!.pageSize).isEqualTo("254x305mm")
        assertThat(env.repo.shot("s1")).isNotNull()
        assertThat(env.db.ops().all().last().kind).isEqualTo(OpKind.ALBUM_META)
    }

    @Test fun `selecting an album switches the current album`() = blocking {
        val a = env.repo.createAlbum("A", PageSize.DEFAULT)
        val b = env.repo.createAlbum("B", PageSize.DEFAULT)
        vm.select(a.id)
        env.settings.lastAlbumId.await { it == a.id }
        vm.select(b.id)
        env.settings.lastAlbumId.await { it == b.id }
    }

    @Test fun `deleting an album from the drawer`() = blocking {
        val a = env.repo.createAlbum("A", PageSize.DEFAULT)
        val b = env.repo.createAlbum("B", PageSize.DEFAULT)
        vm.delete(a.id, alsoOnServer = false)
        vm.delete(b.id, alsoOnServer = true)
        env.repo.observeSummaries().await { it.isEmpty() }
        assertThat(env.db.ops().all().map { it.kind to it.albumId }).containsExactly(OpKind.DELETE_ALBUM to b.id)
    }

    @Test fun `upload state per album in the drawer`() = blocking {
        val a = env.repo.createAlbum("A", PageSize.DEFAULT)
        env.db.ops().deleteForAlbum(a.id)
        var rows = vm.albums.await { it.size == 1 && it[0].summary.pendingOps == 0 }
        assertThat(rows.single().upload).isEqualTo(UploadState.UPLOADED)
        env.repo.addShot(a.id, "s1", env.written("s1"))
        rows = vm.albums.await { it[0].summary.pendingOps > 0 }
        assertThat(rows.single().upload).isEqualTo(UploadState.WAITING)
        running.value = true
        vm.albums.await { it[0].upload == UploadState.UPLOADING }
    }

    @Test fun `albums are listed newest first`() = blocking {
        env.repo.createAlbum("First", PageSize.DEFAULT)
        env.repo.createAlbum("Second", PageSize.DEFAULT)
        val rows = vm.albums.await { it.size == 2 }
        assertThat(rows.map { it.summary.name }).containsExactly("Second", "First").inOrder()
    }
}
