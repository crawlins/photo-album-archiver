package org.bit63.albumarchiver

import androidx.work.ListenableWorker
import androidx.work.testing.TestListenableWorkerBuilder
import com.google.common.truth.Truth.assertThat
import kotlinx.coroutines.runBlocking
import org.bit63.albumarchiver.data.InMemorySettingsStore
import org.bit63.albumarchiver.data.PageSize
import org.bit63.albumarchiver.testing.FakeAlbumServer
import org.bit63.albumarchiver.upload.LastPageLoadWorker
import org.bit63.albumarchiver.upload.UploadWorker
import org.junit.After
import org.junit.Test
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.work.workDataOf
import org.junit.runner.RunWith

/** The WorkManager workers on a real device runtime. */
@RunWith(AndroidJUnit4::class)
class UploadWorkerTest {
    private val fake = FakeAlbumServer().start()
    private val container = TestContainer(app, testSettings = InMemorySettingsStore(fake.url, fake.token))

    @After fun tearDown() {
        closeApp()
        fake.shutdown()
    }

    private fun upload(): ListenableWorker.Result {
        app.container = container
        return runBlocking { TestListenableWorkerBuilder<UploadWorker>(app).build().doWork() }
    }

    @Test fun uploadsTheQueueAndSucceeds() {
        val id = io {
            val a = container.repository.createAlbum("W", PageSize.DEFAULT)
            val t = container.shotStore.newTempFile("s").apply { writeBytes(realJpeg("s")) }
            container.repository.addShot(a.id, "s", container.shotStore.finish(t))
            a.id
        }
        assertThat(upload()).isEqualTo(ListenableWorker.Result.success())
        assertThat(fake.album(id)!!.pages.single().shots.single().id).isEqualTo("s")
        assertThat(io { container.db.ops().all() }).isEmpty()
    }

    @Test fun asksForARetryWhenTheServerFails() {
        io { container.repository.createAlbum("W", PageSize.DEFAULT) }
        fake.failNext += 503
        assertThat(upload()).isEqualTo(ListenableWorker.Result.retry())
    }

    @Test fun stopsWithoutRetryingWhenTheTokenIsRejected() {
        io { container.repository.createAlbum("W", PageSize.DEFAULT) }
        fake.token = "rotated"
        assertThat(upload()).isEqualTo(ListenableWorker.Result.success())
        assertThat(io { container.db.ops().all() }).hasSize(1)
    }

    @Test fun lastPageLoadWorkerLoadsTheLastPage() {
        fake.seed("srv", "S", "a4", listOf(listOf("a" to realJpeg("a")), listOf("b" to realJpeg("b"))))
        io { container.importer.open("srv") }
        app.container = container
        val worker = TestListenableWorkerBuilder<LastPageLoadWorker>(app)
            .setInputData(workDataOf("albumId" to "srv"))
            .build()
        assertThat(runBlocking { worker.doWork() }).isEqualTo(ListenableWorker.Result.success())
        assertThat(io { container.repository.shot("b") }).isNotNull()
    }
}
