package org.bit63.albumarchiver

import android.Manifest
import android.content.Context
import android.media.AudioManager
import android.os.SystemClock
import android.view.KeyEvent
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.rule.GrantPermissionRule
import com.google.common.truth.Truth.assertThat
import org.bit63.albumarchiver.data.Limits
import org.bit63.albumarchiver.data.Page
import org.bit63.albumarchiver.data.PageSize
import org.junit.After
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/** Requirement 6: volume up shoots, volume down starts a page, only on the capture screen. */
@RunWith(AndroidJUnit4::class)
class VolumeKeysTest {
    @get:Rule val compose = createEmptyComposeRule()
    @get:Rule val camera = GrantPermissionRule.grant(Manifest.permission.CAMERA)

    private val container = TestContainer(app)
    private val repo = container.repository
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val audio = app.getSystemService(Context.AUDIO_SERVICE) as AudioManager
    private lateinit var albumId: String
    private var volumeBefore = emptyList<Int>()
    private val streams = listOf(AudioManager.STREAM_MUSIC, AudioManager.STREAM_RING, AudioManager.STREAM_NOTIFICATION)

    /** The volumes the keys could adjust; which stream they pick depends on what is playing. */
    private fun volumes() = streams.map { audio.getStreamVolume(it) }

    @Before fun setUp() {
        albumId = io {
            val a = repo.createAlbum("Keys", PageSize.DEFAULT)
            container.testSettings.setLastAlbumId(a.id)
            a.id
        }
        // A volume in the middle, so a change in either direction would show.
        for (stream in streams) {
            runCatching { audio.setStreamVolume(stream, audio.getStreamMaxVolume(stream) / 2, 0) }
        }
        volumeBefore = volumes()
    }

    @After fun tearDown() = closeApp()

    private fun press(keyCode: Int) {
        dismissSystemDialogs()
        instrumentation.sendKeyDownUpSync(keyCode)
        instrumentation.waitForIdleSync()
    }

    private fun shots() = io { repo.lastPage(albumId)?.shotCount ?: 0 }
    private fun pages() = io { repo.pages(albumId).size }

    private lateinit var scenario: androidx.test.core.app.ActivityScenario<org.bit63.albumarchiver.ui.MainActivity>

    private fun start() {
        scenario = launch(container)
        compose.waitForTag("shutter")
    }

    /** Whether the capture screen currently owns the volume keys. */
    private fun keysOwned(): Boolean {
        var owned = false
        scenario.onActivity { owned = it.volumeKeys.handler != null }
        return owned
    }

    @Test fun volumeUpTakesAShotAndDoesNotChangeTheVolume() {
        start()
        assertThat(keysOwned()).isTrue()
        press(KeyEvent.KEYCODE_VOLUME_UP)
        compose.waitUntil(5000) { shots() == 1 }
        press(KeyEvent.KEYCODE_VOLUME_UP)
        compose.waitUntil(5000) { shots() == 2 }
        assertThat(volumes()).isEqualTo(volumeBefore)
    }

    @Test fun volumeDownStartsTheNextPage() {
        start()
        press(KeyEvent.KEYCODE_VOLUME_UP)
        compose.waitUntil(5000) { shots() == 1 }
        press(KeyEvent.KEYCODE_VOLUME_DOWN)
        compose.waitUntil(5000) { pages() == 2 }
        assertThat(volumes()).isEqualTo(volumeBefore)
    }

    @Test fun holdingVolumeUpTakesOneShot() {
        start()
        val down = SystemClock.uptimeMillis()
        instrumentation.sendKeySync(KeyEvent(down, down, KeyEvent.ACTION_DOWN, KeyEvent.KEYCODE_VOLUME_UP, 0))
        for (r in 1..10) {
            instrumentation.sendKeySync(KeyEvent(down, down + 50L * r, KeyEvent.ACTION_DOWN, KeyEvent.KEYCODE_VOLUME_UP, r))
        }
        instrumentation.sendKeySync(KeyEvent(down, down + 600, KeyEvent.ACTION_UP, KeyEvent.KEYCODE_VOLUME_UP, 0))
        compose.waitUntil(5000) { shots() == 1 }
        Thread.sleep(500)
        assertThat(shots()).isEqualTo(1)
        assertThat(volumes()).isEqualTo(volumeBefore)
    }

    @Test fun keysDoNothingWhileTheDrawerIsOpen() {
        start()
        compose.onNodeWithTag("menu").performClick()
        compose.waitForTag("drawer")
        compose.waitForIdle()
        compose.waitUntil(3000) { !keysOwned() }
        press(KeyEvent.KEYCODE_VOLUME_UP)
        press(KeyEvent.KEYCODE_VOLUME_DOWN)
        Thread.sleep(500)
        assertThat(shots()).isEqualTo(0)
        assertThat(pages()).isEqualTo(0)
    }

    @Test fun keysChangeTheVolumeOnOtherScreens() {
        start()
        compose.onNodeWithTag("menu").performClick()
        compose.waitForTag("drawer")
        compose.onNodeWithTag("drawerSettings").performClick()
        compose.waitForTag("serverUrl")
        compose.waitUntil(3000) { !keysOwned() }
        press(KeyEvent.KEYCODE_VOLUME_UP)
        Thread.sleep(500)
        assertThat(shots()).isEqualTo(0)
        // Back on the capture screen it owns the keys again.
        instrumentation.sendKeyDownUpSync(KeyEvent.KEYCODE_BACK)
        compose.waitForTag("shutter")
        compose.waitUntil(3000) { keysOwned() }
    }

    @Test fun atTheShotLimitVolumeUpIsConsumedWithoutEffect() {
        io {
            repeat(Limits.MAX_SHOTS_PER_PAGE) {
                val t = container.shotStore.newTempFile("s$it").apply { writeBytes(realJpeg("s$it")) }
                repo.addShot(albumId, "s$it", container.shotStore.finish(t))
            }
        }
        start()
        press(KeyEvent.KEYCODE_VOLUME_UP)
        compose.waitForText("Page full", substring = true)
        assertThat(shots()).isEqualTo(25)
        assertThat(volumes()).isEqualTo(volumeBefore)
    }

    @Test fun atThePageLimitVolumeDownIsConsumedWithoutEffect() {
        io { container.db.pages().insertAll((1..Limits.MAX_PAGES_PER_ALBUM).map { Page("p$it", albumId, it, shotCount = 1) }) }
        start()
        press(KeyEvent.KEYCODE_VOLUME_DOWN)
        Thread.sleep(500)
        assertThat(pages()).isEqualTo(500)
        assertThat(volumes()).isEqualTo(volumeBefore)
    }
}
