package org.bit63.albumarchiver

import android.Manifest
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performClick
import androidx.exifinterface.media.ExifInterface
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.rule.GrantPermissionRule
import com.google.common.truth.Truth.assertThat
import org.bit63.albumarchiver.camera.CameraController
import org.bit63.albumarchiver.camera.ShotCamera
import org.bit63.albumarchiver.data.PageSize
import org.junit.After
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File

/**
 * The real CameraX path on the emulator's camera (Requirements 4.1 and 4.3):
 * the shutter saves a full-resolution JPEG with EXIF orientation and focal length.
 */
@RunWith(AndroidJUnit4::class)
class CameraExifTest {
    @get:Rule val compose = createEmptyComposeRule()
    @get:Rule val camera = GrantPermissionRule.grant(Manifest.permission.CAMERA)

    private val realContainer = RealCameraContainer()
    private val repo = realContainer.repository

    @After fun tearDown() = closeApp()

    @Test fun aShotHasEXIFOrientationAndFocalLength() {
        val id = io {
            val a = repo.createAlbum("Camera", PageSize.DEFAULT)
            realContainer.testSettings.setLastAlbumId(a.id)
            a.id
        }
        launch(realContainer)
        compose.waitForTag("preview")
        // Give CameraX time to open the camera and settle exposure.
        Thread.sleep(3000)
        compose.onNodeWithTag("shutter").performClick()
        compose.waitUntil(20000) { io { repo.lastPage(id)?.shotCount } == 1 }

        val shot = io { repo.shots(repo.lastPage(id)!!.id) }.single()
        val file = File(shot.path)
        assertThat(file.length()).isGreaterThan(10_000L)
        val exif = ExifInterface(file)
        assertThat(exif.getAttribute(ExifInterface.TAG_ORIENTATION)).isNotNull()
        assertThat(exif.getAttributeDouble(ExifInterface.TAG_FOCAL_LENGTH, 0.0)).isGreaterThan(0.0)
        assertThat(exif.getAttributeInt(ExifInterface.TAG_IMAGE_WIDTH, 0).coerceAtLeast(exif.getAttributeInt(ExifInterface.TAG_PIXEL_X_DIMENSION, 0)))
            .isGreaterThan(0)
        assertThat(shot.sha256).isEqualTo(org.bit63.albumarchiver.data.ShotStore.sha256(file))
    }

    /** The test container, but with the real CameraX controller. */
    private class RealCameraContainer : TestContainer(app) {
        override fun newCamera(): ShotCamera = CameraController(app)
    }
}
