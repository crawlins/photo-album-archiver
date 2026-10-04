package org.bit63.albumarchiver.ui.review

import com.google.common.truth.Truth.assertThat
import org.bit63.albumarchiver.MainDispatcherRule
import org.bit63.albumarchiver.TestEnv
import org.bit63.albumarchiver.TestViewModels
import org.bit63.albumarchiver.await
import org.bit63.albumarchiver.blocking
import org.bit63.albumarchiver.data.Album
import org.bit63.albumarchiver.data.NextPageResult
import org.bit63.albumarchiver.data.PageSize
import org.bit63.albumarchiver.data.ShotState
import org.bit63.albumarchiver.testing.FakeAlbumServer
import org.bit63.albumarchiver.upload.AlbumImporter
import org.bit63.albumarchiver.upload.PageLoader
import org.bit63.albumarchiver.upload.ServerAccess
import org.bit63.albumarchiver.upload.ShotFetcher
import org.junit.After
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

@RunWith(RobolectricTestRunner::class)
class ReviewViewModelTest {
    @get:Rule val main = MainDispatcherRule()
    private val fake = FakeAlbumServer().start()
    private val env = TestEnv(fake.url, fake.token)
    private val server = ServerAccess(env.settings)
    private val pageLoader = PageLoader(env.repo, server)
    private val shotFetcher = ShotFetcher(env.repo, env.store, server)
    private val vms = TestViewModels()
    private lateinit var albumId: String
    private val pageIds = mutableListOf<String>()

    @After fun tearDown() {
        vms.clear()
        fake.shutdown()
        env.close()
    }

    /** An album with pages of the given shot counts, shot ids "p<page>s<n>". */
    private suspend fun album(vararg counts: Int) {
        albumId = env.repo.createAlbum("A", PageSize.DEFAULT).id
        counts.forEachIndexed { i, n ->
            if (i > 0) pageIds += (env.repo.startNextPage(albumId) as NextPageResult.Started).page.id
            repeat(n) { j -> env.repo.addShot(albumId, "p${i + 1}s${j + 1}", env.written("p${i + 1}s${j + 1}")) }
            if (i == 0) pageIds += env.repo.lastPage(albumId)!!.id
        }
    }

    private fun review(pageIndex: Int, shot: Int = 0) =
        vms.create { ReviewViewModel(env.repo, pageLoader, shotFetcher, albumId, pageIds[pageIndex], shot) }

    @Test fun `opens at the requested shot with the page's shots in order`() = blocking {
        album(2, 3)
        val vm = review(1, 2)
        val shots = vm.shots.await { it.size == 3 }
        assertThat(shots.map { it.id }).containsExactly("p2s1", "p2s2", "p2s3").inOrder()
        assertThat(vm.position.value).isEqualTo(Slot(pageIds[1], 2))
        assertThat(vm.page.await { it != null }!!.position).isEqualTo(2)
    }

    @Test fun `page arrows go to the first shot of the adjacent page and stop at the ends`() = blocking {
        album(1, 2, 1)
        val vm = review(0)
        vm.pages.await { it.size == 3 }
        assertThat(vm.previousPageId()).isNull()
        assertThat(vm.nextPageId()).isEqualTo(pageIds[1])
        vm.onShotShown(0)
        vm.showPage(vm.nextPageId()!!)
        vm.shots.await { it.firstOrNull()?.id == "p2s1" }
        assertThat(vm.position.value).isEqualTo(Slot(pageIds[1], 0))
        vm.showPage(vm.nextPageId()!!)
        assertThat(vm.nextPageId()).isNull()
        assertThat(vm.previousPageId()).isEqualTo(pageIds[1])
    }

    @Test fun `deleting a shot shows the next one`() = blocking {
        album(3)
        val vm = review(0, 1)
        val shots = vm.shots.await { it.size == 3 }
        vm.deleteShot(shots[1])
        vm.shots.await { it.size == 2 }
        assertThat(vm.position.await { it == Slot(pageIds[0], 1) }).isNotNull()
        assertThat(vm.shots.value[1].id).isEqualTo("p1s3")
    }

    @Test fun `deleting the last shot of a page shows the previous one`() = blocking {
        album(3)
        val vm = review(0, 2)
        val shots = vm.shots.await { it.size == 3 }
        vm.deleteShot(shots[2])
        vm.position.await { it == Slot(pageIds[0], 1) }
    }

    @Test fun `deleting an inner page's only shot deletes the page and moves to the next page`() = blocking {
        album(1, 1, 2)
        val vm = review(1)
        val page = vm.page.await { it != null }!!
        assertThat(vm.deletingShotDeletesPage(page)).isTrue()
        vm.deleteShot(vm.shots.await { it.size == 1 }.single())
        vm.pages.await { it.size == 2 }
        vm.position.await { it == Slot(pageIds[2], 0) }
        assertThat(vm.page.await { it?.id == pageIds[2] }!!.position).isEqualTo(2)
    }

    @Test fun `the last page's only shot does not take the page with it`() = blocking {
        album(1, 1)
        val vm = review(1)
        val page = vm.page.await { it != null }!!
        assertThat(vm.deletingShotDeletesPage(page)).isFalse()
    }

    @Test fun `deleting a page shows the following page`() = blocking {
        album(1, 2, 1)
        val vm = review(1)
        vm.deletePage(vm.page.await { it != null }!!)
        vm.position.await { it == Slot(pageIds[2], 0) }
        assertThat(vm.pages.await { it.size == 2 }.map { it.position }).containsExactly(1, 2).inOrder()
    }

    @Test fun `deleting the last page shows the previous page`() = blocking {
        album(2, 1)
        val vm = review(1)
        vm.deletePage(vm.page.await { it != null }!!)
        vm.position.await { it == Slot(pageIds[0], 1) }
    }

    @Test fun `deleting everything returns to the overview`() = blocking {
        album(1)
        val vm = review(0)
        vm.deleteShot(vm.shots.await { it.size == 1 }.single())
        // The album's only page is its last page, so it stays, empty.
        vm.position.await { it == null }
    }

    @Test fun `a page of a server album is loaded when shown and its shots fetched when viewed`() = blocking {
        fake.seed("srv", "S", "a4", listOf(listOf("x1" to FakeAlbumServer.jpeg("x1"), "x2" to FakeAlbumServer.jpeg("x2"))))
        AlbumImporter(env.repo, server) {}.open("srv")
        albumId = "srv"
        pageIds += env.repo.pages("srv").single().id
        val vm = review(0)
        val page = vm.page.await { it != null }!!
        assertThat(page.shotsLoaded).isFalse()
        vm.loader.ensurePage(page)
        val shots = vm.shots.await { it.size == 2 }
        assertThat(shots.all { it.state == ShotState.NOT_DOWNLOADED }).isTrue()
        vm.loader.ensureShot(shots[0])
        vm.shots.await { it[0].state == ShotState.PRESENT }
        assertThat(env.repo.shot("x2")!!.state).isEqualTo(ShotState.NOT_DOWNLOADED)
    }

    @Test fun `a failed load is shown and retried on the next view`() = blocking {
        fake.seed("srv", "S", "a4", listOf(listOf("x1" to FakeAlbumServer.jpeg("x1"))))
        AlbumImporter(env.repo, server) {}.open("srv")
        albumId = "srv"
        pageIds += env.repo.pages("srv").single().id
        val vm = review(0)
        val page = vm.page.await { it != null }!!
        fake.failNext += 503
        vm.loader.ensurePage(page)
        vm.loader.pages.await { it[page.id] == LoadState.FAILED }
        vm.loader.ensurePage(page)
        vm.shots.await { it.size == 1 }
        vm.loader.pages.await { page.id !in it }
    }

    @Test fun `the overview lists pages with first shots and server counts`() = blocking {
        album(2, 1)
        env.repo.importAlbum(Album("srv", "S", "a4", java.time.Instant.EPOCH), listOf("q1" to 7))
        val overview = vms.create { PageOverviewViewModel(env.repo, pageLoader, shotFetcher, albumId) }
        val cells = overview.cells.await { it.size == 2 }
        assertThat(cells.map { it.page.position to it.firstShot?.id }).containsExactly(1 to "p1s1", 2 to "p2s1").inOrder()
        val serverOverview = vms.create { PageOverviewViewModel(env.repo, pageLoader, shotFetcher, "srv") }
        val serverCells = serverOverview.cells.await { it.size == 1 }
        assertThat(serverCells.single().page.shotCount).isEqualTo(7)
        assertThat(serverCells.single().firstShot).isNull()
        assertThat(fake.log).isEmpty()

        overview.deletePage(pageIds[0])
        overview.cells.await { it.size == 1 && it[0].page.position == 1 && it[0].firstShot?.id == "p2s1" }
    }

}
