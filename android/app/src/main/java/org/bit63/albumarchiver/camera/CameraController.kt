package org.bit63.albumarchiver.camera

import android.content.Context
import android.util.Log
import android.view.OrientationEventListener
import android.view.Surface
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageCapture
import androidx.camera.core.ImageCaptureException
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.lifecycle.awaitInstance
import androidx.camera.view.PreviewView
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleOwner
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import java.io.File
import kotlin.coroutines.resume

/** Takes one photo into a file. The capture screen depends on this, so tests can use a fake. */
interface ShotCamera {
    /** Writes a full-resolution JPEG with EXIF to [target]. */
    suspend fun capture(target: File): Result<Unit>
    fun setFlash(on: Boolean)
}

/**
 * CameraX preview and capture (Requirements 3.1, 4.1, 4.3, 4.4, 10.2).
 * `CAPTURE_MODE_MAXIMIZE_QUALITY`, flash off by default, and the target
 * rotation follows how the phone is physically held, so the EXIF orientation
 * is right even though the screen is locked to portrait.
 */
class CameraController(private val context: Context) : ShotCamera {
    private val imageCapture = ImageCapture.Builder()
        .setCaptureMode(ImageCapture.CAPTURE_MODE_MAXIMIZE_QUALITY)
        .setFlashMode(ImageCapture.FLASH_MODE_OFF)
        .build()

    private val orientationListener = object : OrientationEventListener(context) {
        override fun onOrientationChanged(degrees: Int) {
            if (degrees == ORIENTATION_UNKNOWN) return
            imageCapture.targetRotation = rotationFor(degrees)
        }
    }

    private var provider: ProcessCameraProvider? = null

    /** Binds the preview and capture use cases to [owner]; fails when no rear camera could be opened. */
    suspend fun bind(owner: LifecycleOwner, previewView: PreviewView): Result<Unit> = runCatching {
        val provider = ProcessCameraProvider.awaitInstance(context)
        // The provider can complete on a camera thread; binding must happen on the main thread.
        withContext(Dispatchers.Main) { bindOnMain(provider, owner, previewView) }
    }.onFailure { Log.e(TAG, "Could not open the camera", it) }

    private fun bindOnMain(provider: ProcessCameraProvider, owner: LifecycleOwner, previewView: PreviewView) {
        this.provider = provider
        val preview = Preview.Builder().build().also { it.surfaceProvider = previewView.surfaceProvider }
        provider.unbindAll()
        provider.bindToLifecycle(owner, CameraSelector.DEFAULT_BACK_CAMERA, preview, imageCapture)
        orientationListener.enable()
    }

    fun unbind() {
        orientationListener.disable()
        provider?.unbindAll()
    }

    override fun setFlash(on: Boolean) {
        imageCapture.flashMode = if (on) ImageCapture.FLASH_MODE_ON else ImageCapture.FLASH_MODE_OFF
    }

    override suspend fun capture(target: File): Result<Unit> = suspendCancellableCoroutine { cont ->
        target.parentFile?.mkdirs()
        val options = ImageCapture.OutputFileOptions.Builder(target).build()
        imageCapture.takePicture(
            options,
            ContextCompat.getMainExecutor(context),
            object : ImageCapture.OnImageSavedCallback {
                override fun onImageSaved(output: ImageCapture.OutputFileResults) {
                    cont.resume(Result.success(Unit))
                }

                override fun onError(exception: ImageCaptureException) {
                    Log.e(TAG, "Capture failed", exception)
                    target.delete()
                    cont.resume(Result.failure(exception))
                }
            },
        )
    }

    companion object {
        private const val TAG = "CameraController"

        /** Maps a sensor orientation in degrees to the nearest display rotation. */
        fun rotationFor(degrees: Int): Int = when (degrees) {
            in 45 until 135 -> Surface.ROTATION_270
            in 135 until 225 -> Surface.ROTATION_180
            in 225 until 315 -> Surface.ROTATION_90
            else -> Surface.ROTATION_0
        }
    }
}
