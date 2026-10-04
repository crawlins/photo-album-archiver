package org.bit63.albumarchiver.ui

import androidx.compose.runtime.Composable
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import androidx.navigation.navArgument
import org.bit63.albumarchiver.ui.capture.CaptureScreen
import org.bit63.albumarchiver.ui.review.PageOverviewScreen
import org.bit63.albumarchiver.ui.review.ReviewScreen
import org.bit63.albumarchiver.ui.server.ServerAlbumsScreen
import org.bit63.albumarchiver.ui.settings.SettingsScreen

object Routes {
    const val CAPTURE = "capture"
    const val SERVER = "server"
    const val SETTINGS = "settings"
    fun overview(albumId: String) = "overview/$albumId"
    fun review(albumId: String, pageId: String, index: Int = 0) = "review/$albumId/$pageId/$index"
}

@Composable
fun AppNavigation() {
    val nav = rememberNavController()
    NavHost(navController = nav, startDestination = Routes.CAPTURE) {
        composable(Routes.CAPTURE) {
            CaptureScreen(
                openReview = { albumId, pageId, index -> nav.navigate(Routes.review(albumId, pageId, index)) },
                openOverview = { nav.navigate(Routes.overview(it)) },
                openServerAlbums = { nav.navigate(Routes.SERVER) },
                openSettings = { nav.navigate(Routes.SETTINGS) },
            )
        }
        composable(
            "overview/{albumId}",
            arguments = listOf(navArgument("albumId") { type = NavType.StringType }),
        ) { entry ->
            PageOverviewScreen(
                albumId = entry.arguments!!.getString("albumId")!!,
                openReview = { albumId, pageId -> nav.navigate(Routes.review(albumId, pageId)) },
                back = { nav.popBackStack() },
            )
        }
        composable(
            "review/{albumId}/{pageId}/{index}",
            arguments = listOf(
                navArgument("albumId") { type = NavType.StringType },
                navArgument("pageId") { type = NavType.StringType },
                navArgument("index") { type = NavType.IntType },
            ),
        ) { entry ->
            val args = entry.arguments!!
            val albumId = args.getString("albumId")!!
            ReviewScreen(
                albumId = albumId,
                pageId = args.getString("pageId")!!,
                index = args.getInt("index"),
                back = { nav.popBackStack() },
                toOverview = {
                    nav.navigate(Routes.overview(albumId)) {
                        popUpTo(Routes.CAPTURE)
                    }
                },
            )
        }
        composable(Routes.SERVER) {
            ServerAlbumsScreen(
                back = { nav.popBackStack() },
                openSettings = { nav.navigate(Routes.SETTINGS) },
                opened = { nav.popBackStack(Routes.CAPTURE, inclusive = false) },
            )
        }
        composable(Routes.SETTINGS) {
            SettingsScreen(back = { nav.popBackStack() })
        }
    }
}
