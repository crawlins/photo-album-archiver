package org.bit63.albumarchiver.ui

import android.view.KeyEvent
import com.google.common.truth.Truth.assertThat
import org.junit.Test

class VolumeKeyRouterTest {
    private class Recorder : VolumeKeyRouter.Handler {
        val calls = mutableListOf<String>()
        override fun onVolumeUp() { calls += "up" }
        override fun onVolumeDown() { calls += "down" }
    }

    private val router = VolumeKeyRouter()
    private val up = KeyEvent.KEYCODE_VOLUME_UP
    private val down = KeyEvent.KEYCODE_VOLUME_DOWN

    @Test fun `with no handler the keys pass through to the system`() {
        assertThat(router.onKeyDown(up, 0)).isFalse()
        assertThat(router.onKeyUp(up)).isFalse()
        assertThat(router.onKeyDown(down, 0)).isFalse()
    }

    @Test fun `an active handler gets volume up as a shot and volume down as next page`() {
        val r = Recorder().also(router::register)
        assertThat(router.onKeyDown(up, 0)).isTrue()
        assertThat(router.onKeyUp(up)).isTrue()
        assertThat(router.onKeyDown(down, 0)).isTrue()
        assertThat(router.onKeyUp(down)).isTrue()
        assertThat(r.calls).containsExactly("up", "down").inOrder()
    }

    @Test fun `holding a key acts once and consumes the repeats`() {
        val r = Recorder().also(router::register)
        assertThat(router.onKeyDown(up, 0)).isTrue()
        for (n in 1..20) assertThat(router.onKeyDown(up, n)).isTrue()
        assertThat(router.onKeyUp(up)).isTrue()
        assertThat(r.calls).containsExactly("up")
    }

    @Test fun `other keys are never consumed`() {
        val r = Recorder().also(router::register)
        assertThat(router.onKeyDown(KeyEvent.KEYCODE_BACK, 0)).isFalse()
        assertThat(router.onKeyDown(KeyEvent.KEYCODE_CAMERA, 0)).isFalse()
        assertThat(router.onKeyUp(KeyEvent.KEYCODE_VOLUME_MUTE)).isFalse()
        assertThat(r.calls).isEmpty()
    }

    @Test fun `unregistering restores system behaviour`() {
        val r = Recorder().also(router::register)
        router.unregister(r)
        assertThat(router.onKeyDown(up, 0)).isFalse()
        assertThat(r.calls).isEmpty()
    }

    @Test fun `a stale handler cannot unregister its replacement`() {
        val old = Recorder().also(router::register)
        val new = Recorder().also(router::register)
        router.unregister(old)
        assertThat(router.handler).isSameInstanceAs(new)
        router.onKeyDown(down, 0)
        assertThat(new.calls).containsExactly("down")
        assertThat(old.calls).isEmpty()
    }
}
