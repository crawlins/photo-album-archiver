package org.bit63.albumarchiver.upload

import android.util.Log
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.emptyFlow
import kotlinx.coroutines.flow.filter
import kotlinx.coroutines.flow.flatMapLatest
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.merge
import kotlinx.coroutines.launch
import org.bit63.albumarchiver.data.AlbumRepository
import org.bit63.albumarchiver.data.PageFeedback
import org.bit63.albumarchiver.data.warningsJsonOf
import java.time.Clock

/**
 * Fetches an album's page feedback from the server's status and keeps it in
 * the local store (overlap coverage spec, Requirements 4.1 and 4.2).
 *
 * A screen that shows feedback calls [start] with the album it shows; the
 * poll runs at once, again every [periodMs], and whenever an upload run
 * ends, since that is when the server has something new to say. Failures
 * are logged and the next poll retried; nothing is shown (Requirement 4.11).
 */
class FeedbackPoller(
    private val repo: AlbumRepository,
    private val server: ServerAccess,
    private val clock: Clock = Clock.systemUTC(),
    private val periodMs: Long = 10_000,
) {
    /** Fetches the status once; false when no server is configured or the request failed. */
    suspend fun refresh(albumId: String): Boolean {
        val client = server.client() ?: return false
        val status = when (val r = client.albumStatus(albumId)) {
            is ApiResult.Ok -> r.value
            else -> {
                Log.i(TAG, "status of $albumId not fetched: $r")
                return false
            }
        }
        val known = repo.pages(albumId).map { it.id }.toSet()
        val now = clock.instant()
        repo.saveFeedback(
            status.pages.filter { it.id in known }.map {
                PageFeedback(it.id, albumId, it.state, it.lastRun, warningsJsonOf(it.warnings), now)
            },
        )
        return true
    }

    /** Polls the album [albumId] names while [scope] lives; a null album polls nothing. */
    @OptIn(ExperimentalCoroutinesApi::class)
    fun start(scope: CoroutineScope, albumId: Flow<String?>, uploadRunning: Flow<Boolean>): Job = scope.launch {
        albumId.distinctUntilChanged().flatMapLatest { id ->
            if (id == null) emptyFlow() else merge(ticks(), uploadEnds(uploadRunning)).map { id }
        }.collect { refresh(it) }
    }

    private fun ticks(): Flow<Unit> = flow {
        while (true) {
            emit(Unit)
            delay(periodMs)
        }
    }

    private fun uploadEnds(running: Flow<Boolean>): Flow<Unit> {
        var seenRunning = false
        return running.distinctUntilChanged().filter { r ->
            val ended = seenRunning && !r
            if (r) seenRunning = true
            ended
        }.map { }
    }

    private companion object { const val TAG = "FeedbackPoller" }
}
