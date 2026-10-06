package org.bit63.albumarchiver.upload

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update

/**
 * Counts the times a network becomes available, so that a page or shot that
 * failed to load is fetched again when the network returns, not only when it
 * is next shown (Requirement 14.9). Screens key their load effects on [count].
 */
class NetworkReturns {
    private val _count = MutableStateFlow(0)
    val count: StateFlow<Int> = _count

    fun returned() = _count.update { it + 1 }

    companion object {
        /** Watches the system's default network for the life of the app. */
        fun watching(context: Context): NetworkReturns {
            val returns = NetworkReturns()
            context.getSystemService(ConnectivityManager::class.java)?.registerDefaultNetworkCallback(
                object : ConnectivityManager.NetworkCallback() {
                    override fun onAvailable(network: Network) = returns.returned()
                },
            )
            return returns
        }
    }
}
