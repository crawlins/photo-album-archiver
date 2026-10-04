package org.bit63.albumarchiver.ui

import android.view.KeyEvent

/**
 * Routes the volume keys to the capture screen while it is active
 * (Requirement 6). The capture screen registers a [Handler] only while it is
 * resumed with an album open and no drawer or dialog showing; with no
 * handler the keys are not consumed and change the volume as usual.
 *
 * A press acts on its first `ACTION_DOWN`. Auto-repeats and the matching
 * `ACTION_UP` are consumed without acting, so holding a key neither fires
 * twice nor changes the volume.
 */
class VolumeKeyRouter {
    interface Handler {
        fun onVolumeUp()
        fun onVolumeDown()
    }

    @Volatile var handler: Handler? = null
        private set

    fun register(handler: Handler) {
        this.handler = handler
    }

    /** Unregisters [handler] if it is still the active one. */
    fun unregister(handler: Handler) {
        if (this.handler === handler) this.handler = null
    }

    /** Returns true when the key was consumed. */
    fun onKeyDown(keyCode: Int, repeatCount: Int): Boolean {
        if (!isVolumeKey(keyCode)) return false
        val h = handler ?: return false
        if (repeatCount == 0) {
            if (keyCode == KeyEvent.KEYCODE_VOLUME_UP) h.onVolumeUp() else h.onVolumeDown()
        }
        return true
    }

    fun onKeyUp(keyCode: Int): Boolean = isVolumeKey(keyCode) && handler != null

    private fun isVolumeKey(keyCode: Int) =
        keyCode == KeyEvent.KEYCODE_VOLUME_UP || keyCode == KeyEvent.KEYCODE_VOLUME_DOWN
}
