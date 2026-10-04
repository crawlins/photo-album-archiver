package org.bit63.albumarchiver

import android.app.Application
import androidx.camera.camera2.Camera2Config
import androidx.camera.core.CameraSelector
import androidx.camera.core.CameraXConfig
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking

class AlbumArchiverApp : Application(), CameraXConfig.Provider {
    /** Replaced by instrumented tests before an activity starts. */
    lateinit var container: AppContainer

    val appScope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    override fun onCreate() {
        super.onCreate()
        container = AppContainer.production(this)
        // Leftover temp files and shot files without a row are removed before
        // the capture screen can start a new shot (Requirement 10.4).
        runBlocking(Dispatchers.IO) {
            container.shotStore.cleanUp(container.repository.knownPaths())
        }
        // Resumes pending uploads after a reboot or a force stop (Requirement 8.3).
        appScope.launch { container.rescheduleUploads() }
    }

    /**
     * Only the rear camera is used, so CameraX is limited to it: it starts
     * faster and does not wait for a front camera that some devices (and
     * emulators) report but do not have.
     */
    override fun getCameraXConfig(): CameraXConfig =
        CameraXConfig.Builder.fromConfig(Camera2Config.defaultConfig())
            .setAvailableCamerasLimiter(CameraSelector.DEFAULT_BACK_CAMERA)
            .build()
}
