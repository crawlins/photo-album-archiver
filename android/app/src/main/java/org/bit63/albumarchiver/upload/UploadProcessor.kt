package org.bit63.albumarchiver.upload

import android.util.Log
import kotlinx.coroutines.flow.first
import org.bit63.albumarchiver.data.AppDatabase
import org.bit63.albumarchiver.data.OpKind
import org.bit63.albumarchiver.data.SettingsStore
import org.bit63.albumarchiver.data.ShotState
import org.bit63.albumarchiver.data.UploadOp
import java.io.File
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Drains the upload queue in order (Requirement 8.9). The first op that has
 * to be retried stops the run, so nothing overtakes it; the caller retries
 * the whole run with backoff. Responses are handled as in the design's
 * "Upload" table.
 */
class UploadProcessor(
    private val db: AppDatabase,
    private val settings: SettingsStore,
    private val server: ServerAccess,
) {
    enum class Outcome {
        /** The queue is empty. */
        DONE,
        /** An op failed in a way worth retrying; the run should be retried later. */
        RETRY,
        /** The server rejected the token; the queue is paused until Settings change. */
        AUTH_FAILED,
        /** No server is configured; ops stay queued (Requirement 9.5). */
        NO_SERVER,
    }

    /** What to do with one op after trying it. */
    private enum class Step { DONE, DROP, RETRY, RETRY_ONCE, AUTH }

    /** Set by [UploadKicker]s while a run may be in progress, so a change queued during the run is not missed. */
    val moreQueued = AtomicBoolean(false)

    suspend fun drain(): Outcome {
        if (settings.authRejected.first()) return Outcome.AUTH_FAILED
        val client = server.client() ?: return Outcome.NO_SERVER
        val ops = db.ops()
        while (true) {
            moreQueued.set(false)
            val op = ops.head()
            if (op == null) {
                if (moreQueued.get()) continue
                return Outcome.DONE
            }
            when (send(client, op)) {
                Step.DONE -> ops.delete(op.seq)
                Step.DROP -> {
                    Log.w(TAG, "Dropped ${op.kind} for album ${op.albumId} shot ${op.shotId}")
                    ops.delete(op.seq)
                }
                Step.RETRY -> {
                    ops.incrementAttempts(op.seq)
                    return Outcome.RETRY
                }
                Step.RETRY_ONCE -> {
                    if (op.attempts >= 1) {
                        Log.w(TAG, "Dropped ${op.kind} for shot ${op.shotId} after a second 400")
                        ops.delete(op.seq)
                    } else {
                        ops.incrementAttempts(op.seq)
                        return Outcome.RETRY
                    }
                }
                Step.AUTH -> {
                    settings.setAuthRejected(true)
                    return Outcome.AUTH_FAILED
                }
            }
        }
    }

    private suspend fun send(client: ServerClient, op: UploadOp): Step = when (op.kind) {
        OpKind.ALBUM_META -> {
            val meta = metadata(op.albumId, op.pages?.let { p -> p.split(',').filter { it.isNotEmpty() } })
            if (meta == null) Step.DROP else classify(client.putAlbum(op.albumId, meta), isShot = false)
        }
        OpKind.PUT_SHOT -> {
            val shot = op.shotId?.let { db.shots().get(it) }
            val file = shot?.let { File(it.path) }
            if (shot == null || shot.state != ShotState.PRESENT || file == null || !file.isFile) {
                Step.DROP
            } else {
                classify(client.putShot(shot.albumId, shot.pageId, shot.id, file, shot.sha256), isShot = true)
            }
        }
        OpKind.DELETE_SHOT -> classify(client.deleteShot(op.albumId, op.pageId!!, op.shotId!!), isShot = false)
        OpKind.DELETE_PAGE -> classify(client.deletePage(op.albumId, op.pageId!!), isShot = false)
        OpKind.DELETE_ALBUM -> classify(client.deleteAlbum(op.albumId), isShot = false)
        OpKind.MOVE_SHOTS -> when (val r = client.moveShots(op.albumId, op.pageId!!, listOf(op.shotId!!))) {
            // The album is gone from the server: nothing is left to move.
            is ApiResult.HttpError -> if (r.code == 404) Step.DROP else classify(r, isShot = false)
            else -> classify(r, isShot = false)
        }
    }

    private fun classify(result: ApiResult<*>, isShot: Boolean): Step = when (result) {
        is ApiResult.Ok -> Step.DONE
        ApiResult.AuthFailed -> Step.AUTH
        is ApiResult.NetworkError -> Step.RETRY
        is ApiResult.HttpError -> when (result.code) {
            // A hash mismatch can be corruption in transit, so a shot gets one more try.
            400 -> if (isShot) Step.RETRY_ONCE else Step.DROP
            // The app never reuses a shot id and enforces the same limits, so these mean a bug.
            409, 422 -> Step.DROP
            // Far above any phone JPEG; retrying cannot help.
            413 -> Step.DROP
            in 500..599 -> Step.RETRY
            else -> Step.RETRY
        }
    }

    /**
     * The album's current metadata, with [pages] as its page order when given
     * (the order recorded when the op was queued); null when the album is
     * gone from the phone.
     */
    suspend fun metadata(albumId: String, pages: List<String>? = null): AlbumMetadata? {
        val album = db.albums().get(albumId) ?: return null
        return AlbumMetadata(
            name = album.name,
            pageSize = album.pageSize,
            pages = pages ?: db.pages().forAlbum(albumId).map { it.id },
            created = album.createdAt.toString(),
        )
    }

    private companion object { const val TAG = "UploadProcessor" }
}
