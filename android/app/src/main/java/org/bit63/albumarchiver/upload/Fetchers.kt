package org.bit63.albumarchiver.upload

import android.content.Context
import android.util.Log
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withContext
import org.bit63.albumarchiver.AlbumArchiverApp
import org.bit63.albumarchiver.data.Album
import org.bit63.albumarchiver.data.AlbumRepository
import org.bit63.albumarchiver.data.PageSize
import org.bit63.albumarchiver.data.RemoteShot
import org.bit63.albumarchiver.data.SettingsStore
import org.bit63.albumarchiver.data.Shot
import org.bit63.albumarchiver.data.ShotState
import org.bit63.albumarchiver.data.ShotStore
import java.io.File
import java.io.IOException
import java.time.Instant
import java.time.format.DateTimeParseException

private const val TAG = "Fetchers"

private fun parseInstant(text: String): Instant =
    try { Instant.parse(text) } catch (e: DateTimeParseException) { Instant.EPOCH }

/** Fetches one page's shot list on demand and records it as `NOT_DOWNLOADED` rows (Requirement 14.3). */
class PageLoader(private val repo: AlbumRepository, private val server: ServerAccess) {
    suspend fun load(albumId: String, pageId: String): Boolean {
        val client = server.client() ?: return false
        return when (val r = client.getPage(albumId, pageId)) {
            is ApiResult.Ok -> {
                repo.recordPageShots(pageId, r.value.shots.map {
                    RemoteShot(it.id, it.sha256, it.bytes, parseInstant(it.taken))
                })
                true
            }
            is ApiResult.HttpError -> {
                if (r.code == 404) {
                    Log.w(TAG, "Page $pageId is not on the server")
                    repo.recordPageShots(pageId, emptyList())
                    true
                } else false
            }
            else -> false
        }
    }
}

/**
 * Fetches a full shot on demand into the shot's place in [ShotStore],
 * verifies it against the server's SHA-256 (Requirement 14.6) and marks it
 * present. A mismatch discards the file and tries once more.
 */
class ShotFetcher(
    private val repo: AlbumRepository,
    private val store: ShotStore,
    private val server: ServerAccess,
) {
    enum class Result { FETCHED, MISSING, FAILED }

    suspend fun fetch(shot: Shot): Result {
        if (shot.state == ShotState.PRESENT && File(shot.path).isFile) return Result.FETCHED
        val client = server.client() ?: return Result.FAILED
        repeat(2) {
            when (val r = client.getShot(shot.albumId, shot.pageId, shot.id)) {
                is ApiResult.Ok -> {
                    val ok = try {
                        withContext(Dispatchers.IO) {
                            r.value.use { store.writeVerified(it.stream, File(shot.path), shot.sha256) }
                        }
                    } catch (e: IOException) {
                        return Result.FAILED
                    }
                    if (ok) {
                        repo.markShotPresent(shot.id)
                        return Result.FETCHED
                    }
                    Log.w(TAG, "Shot ${shot.id} did not match its hash")
                }
                is ApiResult.HttpError -> {
                    if (r.code == 404) {
                        repo.forgetMissingShot(shot.id)
                        return Result.MISSING
                    }
                    return Result.FAILED
                }
                else -> return Result.FAILED
            }
        }
        return Result.FAILED
    }
}

/**
 * Small previews of shots not on the phone, fetched with `?size=thumb` into
 * the app's cache folder, which the system may clear (Requirement 14.7).
 */
class ThumbFetcher(private val cacheDir: File, private val server: ServerAccess) {
    fun cached(shotId: String): File? = File(cacheDir, "$shotId.jpg").takeIf { it.isFile }

    suspend fun fetch(shot: Shot): File? {
        cached(shot.id)?.let { return it }
        val client = server.client() ?: return null
        val r = client.getShot(shot.albumId, shot.pageId, shot.id, thumb = true)
        if (r !is ApiResult.Ok) return null
        return withContext(Dispatchers.IO) {
            try {
                cacheDir.mkdirs()
                val dest = File(cacheDir, "${shot.id}.jpg")
                val temp = File(cacheDir, "${shot.id}.jpg.tmp")
                r.value.use { d -> temp.outputStream().use { d.stream.copyTo(it) } }
                if (temp.renameTo(dest)) dest else null
            } catch (e: IOException) {
                null
            }
        }
    }
}

/**
 * Opens a server album on the phone (Requirement 14.1): fetches only its
 * metadata, writes the album and its pages in one transaction with no upload
 * ops, then loads the last page in the background.
 */
class AlbumImporter(
    private val repo: AlbumRepository,
    private val server: ServerAccess,
    private val loadLastPage: suspend (albumId: String) -> Unit,
) {
    sealed interface Result {
        data object Opened : Result
        data object NoServer : Result
        data object AuthFailed : Result
        data object Unreachable : Result
        data class Failed(val code: Int) : Result
    }

    suspend fun open(albumId: String): Result {
        if (repo.album(albumId) != null) return Result.Opened
        val client = server.client() ?: return Result.NoServer
        return when (val r = client.getAlbum(albumId)) {
            is ApiResult.Ok -> {
                val a = r.value
                val known = a.pageSize?.takeIf { PageSize.parse(it) != null }
                repo.importAlbum(
                    Album(a.id, a.name, known ?: PageSize.DEFAULT.text, parseInstant(a.created)),
                    a.pages.map { it.id to it.shots },
                    sendMetadata = known == null,
                )
                loadLastPage(a.id)
                Result.Opened
            }
            ApiResult.AuthFailed -> Result.AuthFailed
            is ApiResult.NetworkError -> Result.Unreachable
            is ApiResult.HttpError -> Result.Failed(r.code)
        }
    }
}

/** Loads the shot list and the full shots of an opened album's last page (Requirement 14.2). */
class LastPageLoader(
    private val repo: AlbumRepository,
    private val pages: PageLoader,
    private val shots: ShotFetcher,
) {
    /** True when the whole page is on the phone. */
    suspend fun load(albumId: String): Boolean {
        val page = repo.lastPage(albumId) ?: return true
        if (!page.shotsLoaded && !pages.load(albumId, page.id)) return false
        var ok = true
        for (shot in repo.shots(page.id)) {
            if (shot.state == ShotState.NOT_DOWNLOADED && shots.fetch(shot) == ShotFetcher.Result.FAILED) ok = false
        }
        return ok
    }
}

/** Runs [LastPageLoader] as unique work, so an album opened offline finishes loading by itself. */
class LastPageLoadWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val albumId = inputData.getString(KEY_ALBUM) ?: return Result.failure()
        val container = (applicationContext as AlbumArchiverApp).container
        if (container.repository.album(albumId) == null) return Result.success()
        return if (container.lastPageLoader.load(albumId)) Result.success() else Result.retry()
    }

    companion object {
        private const val KEY_ALBUM = "albumId"

        suspend fun enqueue(context: Context, settings: SettingsStore, albumId: String) {
            val unmetered = settings.unmeteredOnly.first()
            val request = OneTimeWorkRequestBuilder<LastPageLoadWorker>()
                .setInputData(workDataOf(KEY_ALBUM to albumId))
                .setConstraints(
                    Constraints.Builder()
                        .setRequiredNetworkType(if (unmetered) NetworkType.UNMETERED else NetworkType.CONNECTED)
                        .build()
                )
                .build()
            WorkManager.getInstance(context).enqueueUniqueWork("load-$albumId", ExistingWorkPolicy.REPLACE, request)
        }
    }
}
