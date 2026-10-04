package org.bit63.albumarchiver.ui.settings

import com.google.common.truth.Truth.assertThat
import kotlinx.coroutines.flow.first
import org.bit63.albumarchiver.MainDispatcherRule
import org.bit63.albumarchiver.TestViewModels
import org.bit63.albumarchiver.await
import org.bit63.albumarchiver.blocking
import org.bit63.albumarchiver.data.InMemorySettingsStore
import org.bit63.albumarchiver.data.ServerConfig
import org.bit63.albumarchiver.testing.FakeAlbumServer
import org.junit.After
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import java.util.concurrent.atomic.AtomicInteger

@RunWith(RobolectricTestRunner::class)
class SettingsViewModelTest {
    @get:Rule val main = MainDispatcherRule()
    private val fake = FakeAlbumServer().start()
    private val settings = InMemorySettingsStore()
    private val changes = AtomicInteger()
    private val vms = TestViewModels()
    private val vm by lazy { vms.create { SettingsViewModel(settings, { changes.incrementAndGet() }) } }

    @After fun tearDown() {
        vms.clear()
        fake.shutdown()
    }

    @Test fun `loads the stored values`() = blocking {
        settings.setServer("https://x", "t")
        settings.setUnmeteredOnly(false)
        val f = vm.form.await { it.url.isNotEmpty() }
        assertThat(f).isEqualTo(SettingsForm(url = "https://x", token = "t", unmeteredOnly = false))
    }

    @Test fun `url validation and the http warning`() {
        assertThat(SettingsForm(url = "").urlError).isNull()
        assertThat(SettingsForm(url = "nonsense").urlError).isNotNull()
        assertThat(SettingsForm(url = "nonsense").canTest).isFalse()
        assertThat(SettingsForm(url = "http://192.168.1.2:8080").cleartextWarning).isTrue()
        assertThat(SettingsForm(url = "https://archive.example").cleartextWarning).isFalse()
        assertThat(SettingsForm(url = "https://archive.example").canTest).isTrue()
        assertThat(SettingsForm(url = "https://archive.example", testing = true).canTest).isFalse()
    }

    @Test fun `saving stores the server, clears the auth pause and reschedules uploads`() = blocking {
        settings.setAuthRejected(true)
        vm.form.await { true }
        vm.setUrl(" https://archive.example/ ")
        vm.setToken(" tok ")
        vm.save()
        vm.form.await { it.saved }
        assertThat(settings.currentServer()).isEqualTo(ServerConfig("https://archive.example", "tok"))
        assertThat(settings.authRejected.first()).isFalse()
        assertThat(changes.get()).isEqualTo(1)
    }

    @Test fun `an invalid url is not saved`() = blocking {
        vm.form.await { true }
        vm.setUrl("nonsense")
        vm.save()
        Thread.sleep(200)
        assertThat(settings.serverUrl.first()).isEmpty()
    }

    @Test fun `the network choice is saved at once and reschedules uploads`() = blocking {
        vm.form.await { true }
        vm.setUnmeteredOnly(false)
        settings.unmeteredOnly.await { !it }
        vm.setUnmeteredOnly(true)
        settings.unmeteredOnly.await { it }
        assertThat(changes.get()).isEqualTo(2)
    }

    @Test fun `test connection reports success`() = blocking {
        vm.form.await { true }
        vm.setUrl(fake.url)
        vm.setToken(fake.token)
        vm.testConnection()
        assertThat(vm.form.await { it.result != null }.result).isEqualTo(ConnectionResult.OK)
        assertThat(fake.log.single()).isEqualTo("GET /api/v1/ping")
    }

    @Test fun `test connection reports rejected credentials`() = blocking {
        vm.form.await { true }
        vm.setUrl(fake.url)
        vm.setToken("wrong")
        vm.testConnection()
        assertThat(vm.form.await { it.result != null }.result).isEqualTo(ConnectionResult.REJECTED)
    }

    @Test fun `test connection reports an unreachable server`() = blocking {
        vm.form.await { true }
        vm.setUrl("http://127.0.0.1:1")
        vm.testConnection()
        assertThat(vm.form.await { it.result != null }.result).isEqualTo(ConnectionResult.UNREACHABLE)
    }

    @Test fun `test connection reports a server error`() = blocking {
        vm.form.await { true }
        vm.setUrl(fake.url)
        vm.setToken(fake.token)
        fake.failNext += 500
        vm.testConnection()
        assertThat(vm.form.await { it.result != null }.result).isEqualTo(ConnectionResult.ERROR)
    }

    @Test fun `testing uses the typed values without saving them`() = blocking {
        vm.form.await { true }
        vm.setUrl(fake.url)
        vm.setToken(fake.token)
        vm.testConnection()
        vm.form.await { it.result != null }
        assertThat(settings.serverUrl.first()).isEmpty()
        vm.setToken("edited")
        assertThat(vm.form.value.result).isNull()
    }
}
