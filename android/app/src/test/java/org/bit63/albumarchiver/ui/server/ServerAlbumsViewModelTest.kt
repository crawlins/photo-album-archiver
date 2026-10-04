package org.bit63.albumarchiver.ui.server

import com.google.common.truth.Truth.assertThat
import kotlinx.coroutines.flow.first
import org.bit63.albumarchiver.MainDispatcherRule
import org.bit63.albumarchiver.TestEnv
import org.bit63.albumarchiver.TestViewModels
import org.bit63.albumarchiver.await
import org.bit63.albumarchiver.blocking
import org.bit63.albumarchiver.data.PageSize
import org.bit63.albumarchiver.testing.FakeAlbumServer
import org.bit63.albumarchiver.upload.AlbumImporter
import org.bit63.albumarchiver.upload.ServerAccess
import org.bit63.albumarchiver.upload.UploadProcessor
import org.junit.After
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

@RunWith(RobolectricTestRunner::class)
class ServerAlbumsViewModelTest {
    @get:Rule val main = MainDispatcherRule()
    private val fake = FakeAlbumServer().start()
    private val env = TestEnv(fake.url, fake.token)
    private val server = ServerAccess(env.settings)
    private val loads = mutableListOf<String>()
    private val importer = AlbumImporter(env.repo, server) { loads += it }
    private val vms = TestViewModels()

    @After fun tearDown() {
        vms.clear()
        fake.shutdown()
        env.close()
    }

    private fun vm() = vms.create { ServerAlbumsViewModel(server, env.repo, env.settings, importer) }

    private fun seedTwo() {
        fake.seed("one", "One", "a4", listOf(listOf("a" to FakeAlbumServer.jpeg("a")), listOf("b" to FakeAlbumServer.jpeg("b"))))
        fake.seed("two", "Two", "10x12in", listOf(listOf("c" to FakeAlbumServer.jpeg("c"))))
    }

    private suspend fun loaded(vm: ServerAlbumsViewModel) =
        (vm.state.await { it is ServerAlbumsState.Loaded } as ServerAlbumsState.Loaded).rows

    @Test fun `lists every server album with its counts`() = blocking {
        seedTwo()
        val rows = loaded(vm())
        val one = rows.first { it.album.id == "one" }.album
        assertThat(one.name).isEqualTo("One")
        assertThat(one.pageSize).isEqualTo("a4")
        assertThat(one.pages).isEqualTo(2)
        assertThat(one.shots).isEqualTo(2)
        assertThat(rows.none { it.onPhone }).isTrue()
    }

    @Test fun `albums on this phone are marked`() = blocking {
        val a = env.repo.createAlbum("Mine", PageSize.DEFAULT)
        env.repo.addShot(a.id, "s", env.written("s"))
        UploadProcessor(env.db, env.settings, server).drain()
        seedTwo()
        val rows = loaded(vm())
        assertThat(rows.associate { it.album.name to it.onPhone }).containsExactly("Mine", true, "One", false, "Two", false)
    }

    @Test fun `pull to refresh fetches again`() = blocking {
        val vm = vm()
        assertThat(loaded(vm)).isEmpty()
        seedTwo()
        vm.refresh()
        vm.state.await { (it as? ServerAlbumsState.Loaded)?.rows?.size == 2 }
        vm.refreshing.await { !it }
    }

    @Test fun `each error state says which`() = blocking {
        env.settings.setServer("", "")
        assertThat(vm().state.await { it is ServerAlbumsState.Error }).isEqualTo(ServerAlbumsState.Error(ServerProblem.NO_SERVER))
        env.settings.setServer(fake.url, "wrong")
        assertThat(vm().state.await { it is ServerAlbumsState.Error }).isEqualTo(ServerAlbumsState.Error(ServerProblem.AUTH))
        env.settings.setServer("http://127.0.0.1:1", "x")
        assertThat(vm().state.await { it is ServerAlbumsState.Error }).isEqualTo(ServerAlbumsState.Error(ServerProblem.UNREACHABLE))
        env.settings.setServer(fake.url, fake.token)
        fake.failNext += 500
        val vm = vm()
        assertThat(vm.state.await { it is ServerAlbumsState.Error }).isEqualTo(ServerAlbumsState.Error(ServerProblem.OTHER))
        assertThat(ServerProblem.NO_SERVER.toSettings).isTrue()
        assertThat(ServerProblem.AUTH.toSettings).isTrue()
        assertThat(ServerProblem.UNREACHABLE.toSettings).isFalse()
        vm.refresh()
        vm.state.await { it is ServerAlbumsState.Loaded }
    }

    @Test fun `deleting a server-only album removes it from the server and the list`() = blocking {
        seedTwo()
        val vm = vm()
        loaded(vm)
        vm.deleteFromServer("one")
        vm.state.await { (it as ServerAlbumsState.Loaded).rows.map { r -> r.album.id } == listOf("two") }
        assertThat(fake.album("one")).isNull()
    }

    @Test fun `a failed delete leaves the album listed and says so`() = blocking {
        seedTwo()
        val vm = vm()
        loaded(vm)
        fake.failNext += 500
        vm.deleteFromServer("one")
        assertThat(vm.message.await { it != null }).contains("Couldn't delete")
        assertThat(loaded(vm).map { it.album.id }).contains("one")
        assertThat(fake.album("one")).isNotNull()
    }

    @Test fun `an album on this phone is never deleted from this list`() = blocking {
        val a = env.repo.createAlbum("Mine", PageSize.DEFAULT)
        UploadProcessor(env.db, env.settings, server).drain()
        val vm = vm()
        loaded(vm)
        fake.log.clear()
        vm.deleteFromServer(a.id)
        Thread.sleep(300)
        assertThat(fake.log).isEmpty()
        assertThat(fake.album(a.id)).isNotNull()
    }

    @Test fun `opening an album makes it current and marks it on this phone`() = blocking {
        seedTwo()
        val vm = vm()
        loaded(vm)
        val opened = CountDownLatch(1)
        vm.open("two") { opened.countDown() }
        assertThat(opened.await(5, TimeUnit.SECONDS)).isTrue()
        assertThat(env.settings.lastAlbumId.first()).isEqualTo("two")
        assertThat(loads).containsExactly("two")
        vm.state.await { s -> (s as ServerAlbumsState.Loaded).rows.first { it.album.id == "two" }.onPhone }
    }

    @Test fun `a failed open says why and creates nothing`() = blocking {
        seedTwo()
        val vm = vm()
        loaded(vm)
        fake.failNext += 503
        vm.open("two") { error("must not open") }
        assertThat(vm.message.await { it != null }).contains("503")
        assertThat(env.repo.album("two")).isNull()
        vm.clearMessage()
        assertThat(vm.message.value).isNull()
    }
}
