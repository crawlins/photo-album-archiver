package org.bit63.albumarchiver

import android.Manifest
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.assertTextContains
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.compose.ui.test.longClick
import androidx.compose.ui.test.onAllNodesWithText
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
import org.bit63.albumarchiver.data.OpKind
import org.bit63.albumarchiver.data.PageSize
import org.junit.After
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import java.time.LocalDate

/** Requirements 1 and 2: the album drawer. */
@RunWith(AndroidJUnit4::class)
class DrawerTest {
    @get:Rule val compose = createEmptyComposeRule()
    @get:Rule val camera = GrantPermissionRule.grant(Manifest.permission.CAMERA)

    private val container = TestContainer(app)
    private val repo = container.repository

    @After fun tearDown() = closeApp()

    private fun openDrawer() {
        compose.waitForTag("menu")
        compose.onNodeWithTag("menu").performClick()
        compose.waitForTag("drawer")
    }

    @Test fun createAnAlbumFromTheEmptyStateWithTheDefaults() {
        launch(container)
        compose.waitForTag("newAlbum")
        compose.onNodeWithTag("newAlbum").performClick()
        compose.waitForTag("albumNameField")
        val expected = "Album ${LocalDate.now()}"
        compose.onNodeWithTag("albumNameField").assertTextContains(expected)
        compose.onNodeWithTag("confirmAlbum").performClick()
        compose.waitForTag("shutter")
        compose.onNodeWithTag("headerAlbumName").assertTextContains(expected)
        val id = io { container.testSettings.lastAlbumId.first() }!!
        assertThat(io { repo.album(id) }!!.pageSize).isEqualTo("letter")
    }

    @Test fun createFromTheDrawerWithACustomSize() {
        launch(container)
        openDrawer()
        compose.onNodeWithTag("drawerNewAlbum").performClick()
        compose.waitForTag("albumNameField")
        compose.onNodeWithTag("albumNameField").performTextClearance()
        compose.onNodeWithTag("albumNameField").performTextInput("Grandma 1970s")
        compose.onNodeWithText("Custom").performClick()
        compose.onNodeWithTag("confirmAlbum").assertIsNotEnabled()
        assertThat(compose.onAllNodesWithText("Required").fetchSemanticsNodes()).hasSize(2)
        compose.onNodeWithTag("width").performTextInput("254")
        compose.onNodeWithTag("height").performTextInput("abc")
        compose.onNodeWithText("Not a number").assertExists()
        compose.onNodeWithTag("height").performTextClearance()
        compose.onNodeWithTag("height").performTextInput("305")
        compose.onNodeWithText("mm").performClick()
        compose.onNodeWithTag("confirmAlbum").performClick()
        compose.waitForText("Grandma 1970s")
        val id = io { container.testSettings.lastAlbumId.first() }!!
        assertThat(io { repo.album(id) }!!.pageSize).isEqualTo("254x305mm")
    }

    @Test fun aBlankNameCannotBeSaved() {
        launch(container)
        compose.waitForTag("newAlbum")
        compose.onNodeWithTag("newAlbum").performClick()
        compose.waitForTag("albumNameField")
        compose.onNodeWithTag("albumNameField").performTextClearance()
        compose.onNodeWithText("Enter a name").assertExists()
        compose.onNodeWithTag("confirmAlbum").assertIsNotEnabled()
    }

    @Test fun switchingAlbumsFromTheDrawer() {
        val (a, b) = io {
            val a = repo.createAlbum("Alpha", PageSize.DEFAULT)
            val b = repo.createAlbum("Beta", PageSize.Preset.A4)
            container.testSettings.setLastAlbumId(a.id)
            a to b
        }
        launch(container)
        compose.waitForText("Alpha")
        openDrawer()
        compose.onNodeWithTag("album:Beta").performClick()
        compose.waitUntil(5000) { io { container.testSettings.lastAlbumId.first() } == b.id }
        compose.waitUntil(5000) { runCatching { compose.onNodeWithTag("headerAlbumName").assertTextContains("Beta") }.isSuccess }
        assertThat(a.id).isNotEqualTo(b.id)
    }

    @Test fun drawerShowsPageCountsAndUploadState() {
        io {
            val a = repo.createAlbum("Counted", PageSize.DEFAULT)
            val t = container.shotStore.newTempFile("s").apply { writeBytes(realJpeg("s")) }
            repo.addShot(a.id, "s", container.shotStore.finish(t))
            container.testSettings.setLastAlbumId(a.id)
        }
        launch(container)
        openDrawer()
        compose.waitForText("1 page · waiting")
    }

    @Test fun editingAnAlbumKeepsItsShotsAndQueuesMetadata() {
        val id = io {
            val a = repo.createAlbum("Before", PageSize.DEFAULT)
            val t = container.shotStore.newTempFile("s").apply { writeBytes(realJpeg("s")) }
            repo.addShot(a.id, "s", container.shotStore.finish(t))
            container.testSettings.setLastAlbumId(a.id)
            a.id
        }
        launch(container)
        openDrawer()
        compose.onNodeWithTag("album:Before").performTouchInput { longClick() }
        compose.onNodeWithTag("editAlbum").performClick()
        compose.waitForTag("albumNameField")
        compose.onNodeWithTag("albumNameField").performTextClearance()
        compose.onNodeWithTag("albumNameField").performTextInput("After")
        compose.onNodeWithText("A3").performClick()
        compose.onNodeWithText("Save").performClick()
        compose.waitUntil(5000) { io { repo.album(id) }?.name == "After" }
        assertThat(io { repo.album(id) }!!.pageSize).isEqualTo("a3")
        assertThat(io { repo.shot("s") }).isNotNull()
        assertThat(io { container.db.ops().all() }.last().kind).isEqualTo(OpKind.ALBUM_META)
    }

    @Test fun deletingAnAlbumAsksFirstAndLeavesTheServerAloneByDefault() {
        val id = io {
            val a = repo.createAlbum("Doomed", PageSize.DEFAULT)
            container.testSettings.setLastAlbumId(a.id)
            a.id
        }
        launch(container)
        openDrawer()
        compose.onNodeWithTag("album:Doomed").performTouchInput { longClick() }
        compose.onNodeWithTag("deleteAlbum").performClick()
        compose.waitForText("Delete Doomed?")
        compose.onNodeWithText("Also delete from the server").assertExists()
        compose.onNodeWithTag("confirmDelete").performClick()
        compose.waitUntil(5000) { io { repo.album(id) } == null }
        assertThat(io { container.db.ops().all() }).isEmpty()
        compose.waitForTag("emptyState")
    }

    @Test fun deletingWithTheServerOptionQueuesTheServerDeletion() {
        val id = io {
            val a = repo.createAlbum("Gone", PageSize.DEFAULT)
            container.testSettings.setLastAlbumId(a.id)
            a.id
        }
        launch(container)
        openDrawer()
        compose.onNodeWithTag("album:Gone").performTouchInput { longClick() }
        compose.onNodeWithTag("deleteAlbum").performClick()
        compose.onNodeWithTag("alsoOnServer").performClick()
        compose.waitForText("cannot be undone", substring = true)
        compose.onNodeWithTag("confirmDelete").performClick()
        compose.waitUntil(5000) { io { repo.album(id) } == null }
        assertThat(io { container.db.ops().all() }.map { it.kind }).containsExactly(OpKind.DELETE_ALBUM)
    }
}
