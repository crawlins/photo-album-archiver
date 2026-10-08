package org.bit63.albumarchiver

import android.Manifest
import androidx.compose.ui.test.assertIsSelected
import androidx.compose.ui.test.assertTextContains
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.compose.ui.test.longClick
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTouchInput
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.rule.GrantPermissionRule
import com.google.common.truth.Truth.assertThat
import org.bit63.albumarchiver.data.PageSize
import org.junit.After
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/** shot-actions spec: selecting a photo on a thumbnail strip and the actions on it and the photos after it. */
@RunWith(AndroidJUnit4::class)
class ShotActionsTest {
    @get:Rule val compose = createEmptyComposeRule()
    @get:Rule val camera = GrantPermissionRule.grant(Manifest.permission.CAMERA)

    private val container = TestContainer(app)
    private val repo = container.repository
    private lateinit var albumId: String

    @After fun tearDown() = closeApp()

    /** Pages of 2 and 4 shots; the current page is page 2. */
    @Before fun setUp() {
        albumId = io {
            val a = repo.createAlbum("Actions", PageSize.DEFAULT)
            listOf(2, 4).forEachIndexed { i, n ->
                if (i > 0) repo.startNextPage(a.id)
                repeat(n) { j ->
                    val id = "p${i + 1}s${j + 1}"
                    val t = container.shotStore.newTempFile(id).apply { writeBytes(realJpeg(id)) }
                    repo.addShot(a.id, id, container.shotStore.finish(t))
                }
            }
            container.testSettings.setLastAlbumId(a.id)
            a.id
        }
    }

    private fun thumb(i: Int) = compose.onAllNodesWithTag("thumb")[i]

    private fun openMenuOn(i: Int) {
        launch(container)
        compose.waitUntil(5000) { compose.onAllNodesWithTag("thumb").fetchSemanticsNodes().size == 4 }
        thumb(i).performTouchInput { longClick() }
        thumb(i).assertIsSelected()
        thumb(i).performTouchInput { longClick() }
        compose.waitForTag("shotActions")
    }

    @Test fun aLongPressSelectsWithoutOpeningTheReview() {
        launch(container)
        compose.waitUntil(5000) { compose.onAllNodesWithTag("thumb").fetchSemanticsNodes().size == 4 }
        thumb(2).performTouchInput { longClick() }
        thumb(2).assertIsSelected()
        assertThat(compose.onAllNodesWithTag("reviewLabel").fetchSemanticsNodes()).isEmpty()
        assertThat(compose.onAllNodesWithTag("shotActions").fetchSemanticsNodes()).isEmpty()
        // a tap on the preview clears it
        compose.onNodeWithTag("clearSelection").performClick()
        compose.waitUntil(5000) { compose.onAllNodesWithTag("clearSelection").fetchSemanticsNodes().isEmpty() }
    }

    @Test fun theMenuNamesThePhotoAndOffersTheFourActions() {
        openMenuOn(1)
        compose.onNodeWithTag("shotActionsTitle").assertTextContains("Page 2, photo 2 of 4")
        compose.onNodeWithText("Delete this photo").assertExists()
        compose.onNodeWithText("Delete this photo and the 2 after it").assertExists()
        compose.onNodeWithText("Move this photo and the 2 after it to a new page 3").assertExists()
        compose.onNodeWithText("Create a new page with this photo and the 2 after it").assertExists()
    }

    @Test fun actionsThatWouldChangeNothingAreDisabledWithTheirReason() {
        openMenuOn(0)
        compose.onNodeWithTag("actionMoveReason", useUnmergedTree = true).assertTextContains("Already the whole last page")
        compose.onNodeWithTag("actionNewPageReason", useUnmergedTree = true).assertTextContains("Already the whole page")
        compose.onNodeWithTag("actionMove").performClick()
        compose.waitForIdle()
        assertThat(io { repo.pages(albumId) }).hasSize(2)
    }

    @Test fun movingTheEndOfThePageMakesANewCurrentPage() {
        openMenuOn(2)
        compose.onNodeWithTag("actionMove").performClick()
        compose.waitUntil(5000) { io { repo.pages(albumId).size } == 3 }
        compose.waitUntil(5000) {
            runCatching { compose.onNodeWithTag("pageNumber").assertTextContains("Page 3") }.isSuccess
        }
        compose.waitUntil(5000) { compose.onAllNodesWithTag("thumb").fetchSemanticsNodes().size == 2 }
        compose.waitForText("Moved 2 photos to page 3")
    }

    @Test fun deletingTheRunAsksFirst() {
        openMenuOn(1)
        compose.onNodeWithTag("actionDeleteRun").performClick()
        compose.waitForText("Delete 3 photos from page 2?")
        compose.onNodeWithTag("confirmShotAction").performClick()
        compose.waitUntil(5000) { io { repo.lastPage(albumId)!!.shotCount } == 1 }
        assertThat(io { repo.shot("p2s1") }).isNotNull()
    }

    @Test fun volumeKeysAreLeftAloneWhileTheMenuIsOpen() {
        openMenuOn(1)
        dismissSystemDialogs()
        androidx.test.platform.app.InstrumentationRegistry.getInstrumentation()
            .sendKeyDownUpSync(android.view.KeyEvent.KEYCODE_VOLUME_UP)
        compose.waitForIdle()
        assertThat(io { repo.lastPage(albumId)!!.shotCount }).isEqualTo(4)
    }

    @Test fun theReviewStripSplitsAnEarlierPage() {
        launch(container)
        compose.waitForTag("pageNumber")
        compose.onNodeWithTag("pageNumber").performClick()
        compose.waitForTag("pageGrid")
        compose.onNodeWithTag("page:1").performClick()
        compose.waitUntil(5000) { compose.onAllNodesWithTag("reviewThumb").fetchSemanticsNodes().size == 2 }
        val second = compose.onAllNodesWithTag("reviewThumb")[1]
        second.performTouchInput { longClick() }
        second.performTouchInput { longClick() }
        compose.waitForTag("shotActions")
        compose.onNodeWithTag("actionNewPage").performClick()
        compose.waitUntil(5000) {
            runCatching { compose.onNodeWithTag("reviewLabel").assertTextContains("Page 2, shot 1 of 1") }.isSuccess
        }
        assertThat(io { repo.pages(albumId).map { it.shotCount } }).containsExactly(1, 1, 4).inOrder()
    }
}
