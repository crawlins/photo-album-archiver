package org.bit63.albumarchiver.data

import androidx.datastore.preferences.core.PreferenceDataStoreFactory
import com.google.common.truth.Truth.assertThat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.flow.first
import org.bit63.albumarchiver.blocking
import org.junit.After
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

class SettingsStoreTest {
    @get:Rule val tmp = TemporaryFolder()
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    @After fun tearDown() = scope.cancel()

    private fun store(file: File = File(tmp.root, "settings.preferences_pb"), tokens: TokenStore = InMemoryTokenStore()) =
        DataStoreSettingsStore(PreferenceDataStoreFactory.create(scope = scope) { file }, tokens)

    @Test fun `defaults`() = blocking {
        val s = store()
        assertThat(s.lastAlbumId.first()).isNull()
        assertThat(s.serverUrl.first()).isEmpty()
        assertThat(s.unmeteredOnly.first()).isTrue()
        assertThat(s.authRejected.first()).isFalse()
        assertThat(s.currentServer()).isNull()
    }

    @Test fun `last album is recorded and cleared`() = blocking {
        val s = store()
        s.setLastAlbumId("a1")
        assertThat(s.lastAlbumId.first()).isEqualTo("a1")
        s.setLastAlbumId(null)
        assertThat(s.lastAlbumId.first()).isNull()
    }

    @Test fun `a server needs both a URL and a token, and loses its trailing slash`() = blocking {
        val tokens = InMemoryTokenStore()
        val s = store(tokens = tokens)
        s.setServer(" https://archive.example:8080/ ", "")
        assertThat(s.currentServer()).isNull()
        s.setServer("https://archive.example:8080/", "t0k")
        assertThat(s.currentServer()).isEqualTo(ServerConfig("https://archive.example:8080", "t0k"))
        assertThat(tokens.token.first()).isEqualTo("t0k")
    }

    @Test fun `the token is kept out of the preferences file`() = blocking {
        val file = File(tmp.root, "plain.preferences_pb")
        val s = store(file)
        s.setServer("https://x", "very-secret-token")
        assertThat(file.readBytes().decodeToString()).doesNotContain("very-secret-token")
    }

    @Test fun `saving the server clears a rejected-token pause`() = blocking {
        val s = store()
        s.setAuthRejected(true)
        assertThat(s.authRejected.first()).isTrue()
        s.setServer("https://x", "new")
        assertThat(s.authRejected.first()).isFalse()
    }

    @Test fun `settings survive a restart`() = blocking {
        val file = File(tmp.root, "persist.preferences_pb")
        val tokens = InMemoryTokenStore()
        val firstRun = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        DataStoreSettingsStore(PreferenceDataStoreFactory.create(scope = firstRun) { file }, tokens).apply {
            setLastAlbumId("a1")
            setServer("https://x", "t")
            setUnmeteredOnly(false)
        }
        firstRun.coroutineContext[kotlinx.coroutines.Job]!!.cancelAndJoin()
        val again = DataStoreSettingsStore(
            PreferenceDataStoreFactory.create(scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)) { file }, tokens,
        )
        assertThat(again.lastAlbumId.first()).isEqualTo("a1")
        assertThat(again.serverUrl.first()).isEqualTo("https://x")
        assertThat(again.unmeteredOnly.first()).isFalse()
    }
}
