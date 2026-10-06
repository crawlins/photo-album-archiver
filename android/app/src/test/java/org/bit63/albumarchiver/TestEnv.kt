package org.bit63.albumarchiver

import androidx.test.core.app.ApplicationProvider
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withTimeout
import org.bit63.albumarchiver.data.AlbumRepository
import org.bit63.albumarchiver.data.AppDatabase
import org.bit63.albumarchiver.data.InMemorySettingsStore
import org.bit63.albumarchiver.data.ShotStore
import org.bit63.albumarchiver.data.UploadKicker
import java.io.File
import java.nio.file.Files
import java.time.Clock
import java.time.Instant
import java.time.ZoneOffset
import java.util.concurrent.atomic.AtomicInteger

/** A clock that moves on one second every time it is read, so shots get distinct, ordered times. */
class TickingClock(private var now: Instant = Instant.parse("2026-10-04T10:00:00Z")) : Clock() {
    override fun getZone() = ZoneOffset.UTC
    override fun withZone(zone: java.time.ZoneId?) = this
    @Synchronized override fun instant(): Instant = now.also { now = now.plusSeconds(1) }
}

class CountingKicker : UploadKicker {
    val kicks = AtomicInteger()
    override fun kick() { kicks.incrementAndGet() }
}

/** An in-memory database, a temp shot folder and in-memory settings, for Robolectric tests. */
class TestEnv(serverUrl: String = "", token: String = "") : AutoCloseable {
    val context: android.content.Context = ApplicationProvider.getApplicationContext()
    val db: AppDatabase = AppDatabase.inMemory(context)
    val dir: File = Files.createTempDirectory("shots").toFile()
    var freeBytes = 10L * 1024 * 1024 * 1024
    val store = ShotStore(File(dir, "shots")) { freeBytes }
    val settings = InMemorySettingsStore(serverUrl, token)
    val kicker = CountingKicker()
    val clock = TickingClock()
    val repo = AlbumRepository(db, store, kicker, clock)

    /** A finished temp file holding [bytes], ready for [AlbumRepository.addShot]. */
    fun written(id: String, bytes: ByteArray = jpeg(id)): ShotStore.Written {
        val temp = store.newTempFile(id)
        temp.writeBytes(bytes)
        return store.finish(temp)
    }

    override fun close() {
        db.close()
        dir.deleteRecursively()
    }

    companion object {
        /** Bytes that start like a JPEG and differ per [seed]. */
        fun jpeg(seed: String): ByteArray =
            byteArrayOf(0xFF.toByte(), 0xD8.toByte(), 0xFF.toByte(), 0xE0.toByte()) + seed.toByteArray() +
                byteArrayOf(0xFF.toByte(), 0xD9.toByte())
    }
}

/** Waits until [flow] emits a value matching [predicate]. */
suspend fun <T> Flow<T>.await(timeoutMs: Long = 5000, predicate: (T) -> Boolean): T =
    withTimeout(timeoutMs) { first(predicate) }

/** Runs a suspending test body on real threads; Room and the view models use their own dispatchers. */
fun blocking(block: suspend kotlinx.coroutines.CoroutineScope.() -> Unit) {
    kotlinx.coroutines.runBlocking { block() }
}

/**
 * Creates view models through a real [androidx.lifecycle.ViewModelStore], so
 * [clear] cancels their scopes the way leaving a screen does.
 */
class TestViewModels {
    val store = androidx.lifecycle.ViewModelStore()
    var created = 0

    inline fun <reified VM : androidx.lifecycle.ViewModel> create(crossinline make: () -> VM): VM {
        val factory = androidx.lifecycle.viewmodel.viewModelFactory { addInitializer(VM::class) { make() } }
        return androidx.lifecycle.ViewModelProvider.create(store, factory)["vm${created++}", VM::class]
    }

    fun clear() = store.clear()
}
