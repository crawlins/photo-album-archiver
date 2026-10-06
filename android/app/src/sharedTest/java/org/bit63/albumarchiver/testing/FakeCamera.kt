package org.bit63.albumarchiver.testing

import kotlinx.coroutines.CompletableDeferred
import org.bit63.albumarchiver.camera.ShotCamera
import java.io.File
import java.io.IOException
import java.util.concurrent.atomic.AtomicInteger

/** Writes a small JPEG-like file per capture; can fail, or hold a capture open until released. */
class FakeCamera(
    private val makeJpeg: (seed: String) -> ByteArray = FakeAlbumServer::jpeg,
) : ShotCamera {
    val captures = AtomicInteger()
    var fail: Exception? = null
    var flashOn = false
    @Volatile var gate: CompletableDeferred<Unit>? = null

    override suspend fun capture(target: File): Result<Unit> {
        captures.incrementAndGet()
        gate?.await()
        fail?.let { return Result.failure(it) }
        return try {
            target.parentFile?.mkdirs()
            target.writeBytes(makeJpeg(target.name + captures.get()))
            Result.success(Unit)
        } catch (e: IOException) {
            Result.failure(e)
        }
    }

    override fun setFlash(on: Boolean) { flashOn = on }
}
