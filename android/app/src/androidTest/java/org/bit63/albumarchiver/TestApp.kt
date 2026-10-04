package org.bit63.albumarchiver

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import androidx.compose.ui.semantics.getOrNull
import androidx.compose.ui.test.SemanticsNodeInteraction
import androidx.compose.ui.test.junit4.ComposeTestRule
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onAllNodesWithText
import androidx.test.core.app.ActivityScenario
import androidx.test.core.app.ApplicationProvider
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.uiautomator.By
import androidx.test.uiautomator.UiDevice
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import org.bit63.albumarchiver.camera.ShotCamera
import org.bit63.albumarchiver.data.AppDatabase
import org.bit63.albumarchiver.data.InMemorySettingsStore
import org.bit63.albumarchiver.data.ShotStore
import org.bit63.albumarchiver.data.UploadKicker
import org.bit63.albumarchiver.testing.FakeCamera
import org.bit63.albumarchiver.ui.MainActivity
import java.io.ByteArrayOutputStream
import java.io.File
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicLong

/**
 * The app wired with an in-memory database, in-memory settings, a fresh shot
 * folder and a fake camera. With [drainUploads], each change is uploaded
 * straight away by the real [org.bit63.albumarchiver.upload.UploadProcessor],
 * standing in for WorkManager.
 */
open class TestContainer(
    context: Context,
    val testSettings: InMemorySettingsStore = InMemorySettingsStore(),
    val camera: ShotCamera = FakeCamera(::realJpeg),
    private val drainUploads: Boolean = false,
) : AppContainer(
    context = context,
    db = AppDatabase.inMemory(context),
    settings = testSettings,
    shotStore = ShotStore(freshDir(context, "shots")) { freeBytes.get() },
    thumbDir = freshDir(context, "thumbs"),
) {
    val kicks = AtomicInteger()
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    override val uploadKicker: UploadKicker = UploadKicker {
        kicks.incrementAndGet()
        if (drainUploads) scope.launch { uploadProcessor.drain() }
    }
    override val uploadRunning: Flow<Boolean> = MutableStateFlow(false)
    val lastPageLoads = mutableListOf<String>()

    override suspend fun loadLastPage(albumId: String) {
        lastPageLoads += albumId
        lastPageLoader.load(albumId)
    }

    override suspend fun rescheduleUploads() {
        if (drainUploads) uploadProcessor.drain()
    }

    override fun newCamera(): ShotCamera = camera

    companion object {
        private fun freshDir(context: Context, name: String) =
            File(context.cacheDir, "test-$name-${System.nanoTime()}").apply { mkdirs() }
    }
}

/** What the test shot store reports as free space; reset by [launch]. */
val freeBytes = AtomicLong(10L * 1024 * 1024 * 1024)

/** A real, decodable JPEG with a different colour per seed, so thumbnails render. */
fun realJpeg(seed: String): ByteArray {
    val bmp = Bitmap.createBitmap(64, 48, Bitmap.Config.ARGB_8888)
    Canvas(bmp).apply {
        drawColor(Color.rgb(seed.hashCode() and 0xFF, (seed.hashCode() shr 8) and 0xFF, 160))
        drawText(seed.take(6), 4f, 24f, Paint().apply { color = Color.WHITE })
    }
    return ByteArrayOutputStream().also { bmp.compress(Bitmap.CompressFormat.JPEG, 90, it) }.toByteArray()
}

val app: AlbumArchiverApp get() = ApplicationProvider.getApplicationContext()

private var scenario: ActivityScenario<MainActivity>? = null

/**
 * Dismisses a "System UI isn't responding" dialog, which a slow emulator
 * sometimes shows: it takes the input focus, so injected keys never reach
 * the app.
 */
fun dismissSystemDialogs() {
    val device = UiDevice.getInstance(InstrumentationRegistry.getInstrumentation())
    repeat(3) {
        val wait = device.findObject(By.res("android", "aerr_wait")) ?: return
        wait.click()
        device.waitForIdle()
    }
}

/** Installs [container] and launches the app on it. */
fun launch(container: TestContainer): ActivityScenario<MainActivity> {
    dismissSystemDialogs()
    app.container = container
    return ActivityScenario.launch(MainActivity::class.java).also { scenario = it }
}

/** Closes the activity a test launched, so nothing from it outlives the test. */
fun closeApp() {
    scenario?.close()
    scenario = null
}

fun <T> io(block: suspend CoroutineScope.() -> T): T = runBlocking(Dispatchers.IO) { block() }

fun ComposeTestRule.waitForTag(tag: String, timeoutMs: Long = 5000) =
    waitUntil(timeoutMs) { onAllNodesWithTag(tag, useUnmergedTree = true).fetchSemanticsNodes().isNotEmpty() }

fun ComposeTestRule.waitForText(text: String, timeoutMs: Long = 5000, substring: Boolean = false) =
    waitUntil(timeoutMs) { onAllNodesWithText(text, substring = substring).fetchSemanticsNodes().isNotEmpty() }

fun ComposeTestRule.waitGone(text: String, timeoutMs: Long = 5000) =
    waitUntil(timeoutMs) { onAllNodesWithText(text).fetchSemanticsNodes().isEmpty() }

fun SemanticsNodeInteraction.textOf(): String =
    fetchSemanticsNode().config.let { c ->
        c.getOrNull(androidx.compose.ui.semantics.SemanticsProperties.Text)?.joinToString { it.text } ?: ""
    }
