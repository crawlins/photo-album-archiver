package org.bit63.albumarchiver

import android.Manifest
import androidx.compose.ui.test.assertTextContains
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.compose.ui.test.longClick
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTextClearance
import androidx.compose.ui.test.performTextInput
import androidx.compose.ui.test.performTouchInput
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.rule.GrantPermissionRule
import com.google.common.truth.Truth.assertThat
import kotlinx.coroutines.flow.first
import org.bit63.albumarchiver.data.InMemorySettingsStore
import org.bit63.albumarchiver.data.PageSize
import org.bit63.albumarchiver.data.ShotState
import org.bit63.albumarchiver.testing.FakeAlbumServer
import org.junit.After
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/** Settings, uploads, server albums and opening a server album, against the fake server on the device. */
@RunWith(AndroidJUnit4::class)
class ServerTest {
    @get:Rule val compose = createEmptyComposeRule()
    @get:Rule val camera = GrantPermissionRule.grant(Manifest.permission.CAMERA)

    private val fake = FakeAlbumServer().start()
    private val container = TestContainer(
        app,
        testSettings = InMemorySettingsStore(fake.url, fake.token),
        drainUploads = true,
    )
    private val repo = container.repository

    @After fun tearDown() {
        closeApp()
        fake.shutdown()
    }

    private fun openDrawerItem(tag: String) {
        compose.waitForTag("menu")
        compose.onNodeWithTag("menu").performClick()
        compose.waitForTag("drawer")
        compose.onNodeWithTag(tag).performClick()
    }

    @Test fun shotsTakenOnThePhoneReachTheServer() {
        val id = io {
            val a = repo.createAlbum("Uploaded", PageSize.Preset.A4)
            container.testSettings.setLastAlbumId(a.id)
            a.id
        }
        launch(container)
        compose.waitForTag("shutter")
        compose.onNodeWithTag("shutter").performClick()
        compose.waitUntil(5000) { io { repo.lastPage(id)?.shotCount } == 1 }
        compose.onNodeWithTag("shutter").performClick()
        compose.waitUntil(10000) { fake.album(id)?.pages?.singleOrNull()?.shots?.size == 2 }
        compose.onNodeWithTag("nextPage").performClick()
        compose.waitUntil(10000) { fake.album(id)?.pages?.size == 2 }
        val stored = fake.album(id)!!
        assertThat(stored.name).isEqualTo("Uploaded")
        assertThat(stored.pageSize).isEqualTo("a4")
        val local = io { repo.shots(repo.pages(id).first().id) }
        assertThat(stored.pages.first().shots.map { it.sha256 }).isEqualTo(local.map { it.sha256 })
        compose.waitUntil(5000) { compose.onAllNodesWithTag("pendingUploads").fetchSemanticsNodes().isEmpty() }
    }

    @Test fun settingsTestConnectionAndHttpWarning() {
        io { container.testSettings.setServer("", "") }
        launch(container)
        openDrawerItem("drawerSettings")
        compose.waitForTag("serverUrl")
        compose.onNodeWithTag("serverUrl").performTextInput(fake.url)
        compose.waitForTag("httpWarning")
        compose.onNodeWithTag("token").performTextInput("wrong")
        compose.onNodeWithTag("testConnection").performClick()
        compose.waitForTag("connectionResult")
        compose.onNodeWithTag("connectionResult").assertTextContains("rejected", substring = true)
        compose.onNodeWithTag("token").performTextClearance()
        compose.onNodeWithTag("token").performTextInput(fake.token)
        compose.onNodeWithTag("testConnection").performClick()
        compose.waitUntil(5000) {
            runCatching { compose.onNodeWithTag("connectionResult").assertTextContains("Connected.") }.isSuccess
        }
        compose.onNodeWithTag("save").performClick()
        compose.waitForText("Saved.")
        assertThat(io { container.testSettings.currentServer() }!!.token).isEqualTo(fake.token)
    }

    @Test fun settingsReportAnUnreachableServer() {
        io { container.testSettings.setServer("", "") }
        launch(container)
        openDrawerItem("drawerSettings")
        compose.waitForTag("serverUrl")
        compose.onNodeWithTag("serverUrl").performTextInput("http://127.0.0.1:1")
        compose.onNodeWithTag("testConnection").performClick()
        compose.waitUntil(10000) {
            runCatching { compose.onNodeWithTag("connectionResult").assertTextContains("Can't reach", substring = true) }.isSuccess
        }
    }

    @Test fun serverAlbumsAreListedAndThisPhonesAreMarked() {
        io {
            val a = repo.createAlbum("Mine", PageSize.DEFAULT)
            container.uploadProcessor.drain()
            container.testSettings.setLastAlbumId(a.id)
        }
        fake.seed("other", "From another phone", "a4", listOf(listOf("x" to realJpeg("x"))))
        launch(container)
        openDrawerItem("drawerServerAlbums")
        compose.waitForTag("serverAlbums")
        compose.onNodeWithText("From another phone").assertExists()
        compose.onNodeWithText("A4 · 1 pages · 1 shots").assertExists()
        compose.onNodeWithTag("onPhone", useUnmergedTree = true).assertExists()
        assertThat(compose.onAllNodesWithTag("onPhone", useUnmergedTree = true).fetchSemanticsNodes()).hasSize(1)
    }

    @Test fun serverAlbumsErrorPointsToSettings() {
        io { container.testSettings.setServer(fake.url, "wrong") }
        launch(container)
        compose.waitForTag("newAlbum")
        openDrawerItem("drawerServerAlbums")
        compose.waitForTag("serverError")
        compose.onNodeWithText("The server rejected the access token.").assertExists()
        compose.onNodeWithTag("toSettings").performClick()
        compose.waitForTag("serverUrl")
    }

    @Test fun serverAlbumsOfflineOffersRetry() {
        io { container.testSettings.setServer("http://127.0.0.1:1", "x") }
        launch(container)
        openDrawerItem("drawerServerAlbums")
        compose.waitForTag("serverError", 10000)
        compose.onNodeWithText("Retry").assertExists()
    }

    @Test fun deletingAServerOnlyAlbumAsksFirst() {
        fake.seed("old", "Old album", "a4", listOf(listOf("x" to realJpeg("x")), listOf("y" to realJpeg("y"))))
        launch(container)
        openDrawerItem("drawerServerAlbums")
        compose.waitForTag("serverAlbum:Old album")
        compose.onNodeWithTag("serverAlbum:Old album").performTouchInput { longClick() }
        compose.onNodeWithText("Delete from server").performClick()
        compose.waitForText("Delete Old album from the server?")
        compose.onNodeWithText("Its 2 pages", substring = true).assertExists()
        compose.onNodeWithTag("confirmDelete").performClick()
        compose.waitUntil(5000) { fake.album("old") == null }
        compose.waitUntil(5000) { compose.onAllNodesWithTag("serverAlbum:Old album").fetchSemanticsNodes().isEmpty() }
    }

    @Test fun openingAServerAlbumContinuesItOnThePhone() {
        fake.seed(
            "srv", "Grandma", "a4",
            listOf(
                listOf("a1" to realJpeg("a1"), "a2" to realJpeg("a2")),
                listOf("b1" to realJpeg("b1"), "b2" to realJpeg("b2"), "b3" to realJpeg("b3")),
            ),
        )
        launch(container)
        openDrawerItem("drawerServerAlbums")
        compose.waitForTag("serverAlbum:Grandma")
        compose.onNodeWithTag("serverAlbum:Grandma").performClick()
        compose.waitForTag("shutter")
        compose.waitForText("Grandma")
        compose.onNodeWithTag("pageNumber").assertTextContains("Page 2")
        compose.waitUntil(10000) { compose.onAllNodesWithTag("thumb").fetchSemanticsNodes().size == 3 }
        assertThat(io { container.testSettings.lastAlbumId.first() }).isEqualTo("srv")
        val pages = io { repo.pages("srv") }
        assertThat(pages.first().shotsLoaded).isFalse()
        assertThat(io { repo.shots(pages.last().id) }.all { it.state == ShotState.PRESENT }).isTrue()

        // A new shot goes to the opened album's last page and is the only one uploaded.
        fake.log.clear()
        compose.onNodeWithTag("shutter").performClick()
        compose.waitUntil(10000) { fake.album("srv")!!.pages.last().shots.size == 4 }
        assertThat(fake.log.filter { it.startsWith("PUT") }).hasSize(1)

        // The overview shows the server's count for the unloaded page, then loads it on sight.
        compose.onNodeWithTag("pageNumber").performClick()
        compose.waitForTag("pageGrid")
        compose.onNodeWithText("2 shots").assertExists()
        compose.waitUntil(10000) { io { repo.page(pages.first().id)!!.shotsLoaded } }
        compose.onNodeWithTag("page:1").performClick()
        compose.waitUntil(10000) { io { repo.shots(pages.first().id).first().state } == ShotState.PRESENT }
        assertThat(fake.log.any { it.endsWith("/shots/a1?size=thumb") }).isTrue()
        assertThat(fake.log.any { it.endsWith("/shots/a1") }).isTrue()
    }
}
