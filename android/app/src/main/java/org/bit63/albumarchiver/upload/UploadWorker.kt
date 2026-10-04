package org.bit63.albumarchiver.upload

import android.content.Context
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkInfo
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import org.bit63.albumarchiver.AlbumArchiverApp
import org.bit63.albumarchiver.data.SettingsStore
import org.bit63.albumarchiver.data.UploadKicker
import java.util.concurrent.TimeUnit

/** Drains the upload queue in the background; WorkManager keeps it going after the app closes and across reboots (Requirement 8.3). */
class UploadWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val container = (applicationContext as AlbumArchiverApp).container
        return when (container.uploadProcessor.drain()) {
            UploadProcessor.Outcome.RETRY -> Result.retry()
            else -> Result.success()
        }
    }
}

/**
 * Enqueues [UploadWorker] as unique work with the network constraint from
 * Settings and exponential backoff from 30 s (Requirements 8.2 to 8.4).
 */
class UploadScheduler(
    private val context: Context,
    private val settings: SettingsStore,
    private val processor: () -> UploadProcessor,
) : UploadKicker {
    private val workManager get() = WorkManager.getInstance(context)

    @Volatile private var unmeteredOnly = true

    /** Re-reads the network setting and restarts the work with it; called at start and when Settings change. */
    suspend fun reschedule() {
        unmeteredOnly = settings.unmeteredOnly.first()
        enqueue(ExistingWorkPolicy.REPLACE)
    }

    override fun kick() {
        processor().moreQueued.set(true)
        enqueue(ExistingWorkPolicy.KEEP)
    }

    private fun enqueue(policy: ExistingWorkPolicy) {
        val request = OneTimeWorkRequestBuilder<UploadWorker>()
            .setConstraints(
                Constraints.Builder()
                    .setRequiredNetworkType(if (unmeteredOnly) NetworkType.UNMETERED else NetworkType.CONNECTED)
                    .build()
            )
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS)
            .build()
        workManager.enqueueUniqueWork(WORK_NAME, policy, request)
    }

    /** True while an upload run is in progress, for the drawer's "uploading" state. */
    val running: Flow<Boolean>
        get() = workManager.getWorkInfosForUniqueWorkFlow(WORK_NAME)
            .map { infos -> infos.any { it.state == WorkInfo.State.RUNNING } }

    companion object { const val WORK_NAME = "upload" }
}
