package org.bit63.albumarchiver.upload

import android.content.Context
import android.net.ConnectivityManager
import androidx.test.core.app.ApplicationProvider
import com.google.common.truth.Truth.assertThat
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf

@RunWith(RobolectricTestRunner::class)
class NetworkReturnsTest {
    @Test fun `each time the default network becomes available the count goes up`() {
        val context: Context = ApplicationProvider.getApplicationContext()
        val returns = NetworkReturns.watching(context)
        val cm = context.getSystemService(ConnectivityManager::class.java)
        val callbacks = shadowOf(cm).networkCallbacks
        assertThat(callbacks).hasSize(1)
        val before = returns.count.value
        callbacks.single().onAvailable(cm.activeNetwork!!)
        callbacks.single().onAvailable(cm.activeNetwork!!)
        assertThat(returns.count.value).isEqualTo(before + 2)
    }
}
