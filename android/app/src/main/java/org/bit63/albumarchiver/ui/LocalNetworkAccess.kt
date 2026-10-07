package org.bit63.albumarchiver.ui

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.platform.LocalContext
import androidx.core.content.ContextCompat

/**
 * From Android 17, an app needs the runtime ACCESS_LOCAL_NETWORK permission
 * to open connections to addresses on the local network. The server usually
 * lives on the user's LAN, so without it every upload and "Test connection"
 * fails as if the server were down. Earlier versions need nothing.
 */
fun hasLocalNetworkAccess(context: Context): Boolean =
    Build.VERSION.SDK_INT < Build.VERSION_CODES.CINNAMON_BUN ||
        ContextCompat.checkSelfPermission(context, Manifest.permission.ACCESS_LOCAL_NETWORK) ==
        PackageManager.PERMISSION_GRANTED

/**
 * Returns a function that runs an action once local network access has been
 * asked for. The action runs whatever the answer: if the user declines, the
 * connection fails and the screen reports the server as unreachable.
 */
@Composable
fun rememberLocalNetworkAccess(): (() -> Unit) -> Unit {
    val context = LocalContext.current
    val holder = remember { arrayOfNulls<() -> Unit>(1) }
    val launcher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {
        holder[0]?.invoke()
        holder[0] = null
    }
    return remember(launcher) {
        { action ->
            if (hasLocalNetworkAccess(context)) {
                action()
            } else {
                holder[0] = action
                launcher.launch(Manifest.permission.ACCESS_LOCAL_NETWORK)
            }
        }
    }
}
