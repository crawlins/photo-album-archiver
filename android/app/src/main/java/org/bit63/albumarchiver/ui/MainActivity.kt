package org.bit63.albumarchiver.ui

import android.os.Bundle
import android.view.KeyEvent
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.staticCompositionLocalOf
import org.bit63.albumarchiver.AlbumArchiverApp
import org.bit63.albumarchiver.AppContainer

val LocalContainer = staticCompositionLocalOf<AppContainer> { error("No AppContainer") }
val LocalVolumeKeys = staticCompositionLocalOf<VolumeKeyRouter> { error("No VolumeKeyRouter") }

/**
 * The single activity. It is locked to portrait in the manifest
 * (Requirement 10.2) and forwards the volume keys to [VolumeKeyRouter],
 * which consumes them only while the capture screen is active (Requirement 6).
 */
class MainActivity : ComponentActivity() {
    val volumeKeys = VolumeKeyRouter()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        val container = (application as AlbumArchiverApp).container
        setContent {
            CompositionLocalProvider(LocalContainer provides container, LocalVolumeKeys provides volumeKeys) {
                AppTheme { AppNavigation() }
            }
        }
    }

    override fun onKeyDown(keyCode: Int, event: KeyEvent): Boolean =
        volumeKeys.onKeyDown(keyCode, event.repeatCount) || super.onKeyDown(keyCode, event)

    override fun onKeyUp(keyCode: Int, event: KeyEvent): Boolean =
        volumeKeys.onKeyUp(keyCode) || super.onKeyUp(keyCode, event)
}
