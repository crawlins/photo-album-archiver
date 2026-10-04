package org.bit63.albumarchiver

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExecutorCoroutineDispatcher
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.asCoroutineDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import org.junit.rules.TestWatcher
import org.junit.runner.Description
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/** Runs `Dispatchers.Main` (and so `viewModelScope`) on a real background thread. */
@OptIn(ExperimentalCoroutinesApi::class)
class MainDispatcherRule : TestWatcher() {
    private lateinit var executor: ExecutorService
    private lateinit var dispatcher: ExecutorCoroutineDispatcher

    override fun starting(description: Description) {
        executor = Executors.newSingleThreadExecutor { Thread(it, "test-main") }
        dispatcher = executor.asCoroutineDispatcher()
        Dispatchers.setMain(dispatcher)
    }

    override fun finished(description: Description) {
        dispatcher.close()
        executor.awaitTermination(2, TimeUnit.SECONDS)
        // Work cancelled a moment ago may still be resolving Dispatchers.Main on another thread.
        repeat(20) {
            try {
                Dispatchers.resetMain()
                return
            } catch (e: IllegalStateException) {
                Thread.sleep(50)
            }
        }
    }
}
