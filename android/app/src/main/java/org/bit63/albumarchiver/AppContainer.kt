package org.bit63.albumarchiver

import android.content.Context
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import org.bit63.albumarchiver.camera.CameraController
import org.bit63.albumarchiver.camera.ShotCamera
import org.bit63.albumarchiver.data.AlbumRepository
import org.bit63.albumarchiver.data.AppDatabase
import org.bit63.albumarchiver.data.DataStoreSettingsStore
import org.bit63.albumarchiver.data.EncryptedTokenStore
import org.bit63.albumarchiver.data.SettingsStore
import org.bit63.albumarchiver.data.ShotStore
import org.bit63.albumarchiver.data.UploadKicker
import org.bit63.albumarchiver.upload.AlbumImporter
import org.bit63.albumarchiver.upload.LastPageLoadWorker
import org.bit63.albumarchiver.upload.LastPageLoader
import org.bit63.albumarchiver.upload.PageLoader
import org.bit63.albumarchiver.upload.ServerAccess
import org.bit63.albumarchiver.upload.ShotFetcher
import org.bit63.albumarchiver.upload.ThumbFetcher
import org.bit63.albumarchiver.upload.UploadProcessor
import org.bit63.albumarchiver.upload.UploadScheduler
import java.io.File
import java.time.Clock

/**
 * Wires the app together by hand. Tests build one with fakes (an in-memory
 * database, a fake camera, a no-op upload kicker) and install it on the
 * [AlbumArchiverApp] before launching the activity.
 */
open class AppContainer(
    val context: Context,
    val db: AppDatabase,
    val settings: SettingsStore,
    val shotStore: ShotStore,
    thumbDir: File,
    val clock: Clock = Clock.systemUTC(),
) {
    val server = ServerAccess(settings)
    val uploadProcessor = UploadProcessor(db, settings, server)

    /** Starts uploads after a change. Tests override this with a fake. */
    open val uploadKicker: UploadKicker by lazy { scheduler }
    open val uploadRunning: Flow<Boolean> by lazy { scheduler.running }

    private val scheduler by lazy { UploadScheduler(context, settings) { uploadProcessor } }

    val repository by lazy { AlbumRepository(db, shotStore, { uploadKicker.kick() }, clock) }
    val pageLoader by lazy { PageLoader(repository, server) }
    val shotFetcher by lazy { ShotFetcher(repository, shotStore, server) }
    val thumbFetcher = ThumbFetcher(thumbDir, server)
    val lastPageLoader by lazy { LastPageLoader(repository, pageLoader, shotFetcher) }
    val importer by lazy { AlbumImporter(repository, server) { loadLastPage(it) } }

    /** Loads an opened album's last page; WorkManager in the app, overridable in tests. */
    open suspend fun loadLastPage(albumId: String) = LastPageLoadWorker.enqueue(context, settings, albumId)

    /** Restarts the upload work with the current network setting. */
    open suspend fun rescheduleUploads() = scheduler.reschedule()

    /** A camera for the capture screen. */
    open fun newCamera(): ShotCamera = CameraController(context)

    companion object {
        private val Context.dataStore by preferencesDataStore(name = "settings")

        fun production(context: Context): AppContainer {
            val app = context.applicationContext
            return AppContainer(
                context = app,
                db = AppDatabase.open(app),
                settings = DataStoreSettingsStore(app.dataStore, EncryptedTokenStore(app)),
                shotStore = ShotStore(File(app.filesDir, "shots")),
                thumbDir = File(app.cacheDir, "thumbs"),
            )
        }
    }
}
