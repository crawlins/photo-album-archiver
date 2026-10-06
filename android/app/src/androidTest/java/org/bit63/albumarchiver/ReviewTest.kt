package org.bit63.albumarchiver

import android.Manifest
import androidx.compose.ui.test.assertTextContains
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.compose.ui.test.longClick
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onFirst
import androidx.compose.ui.test.onLast
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.test.swipeLeft
import androidx.compose.ui.test.swipeRight
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.rule.GrantPermissionRule
import com.google.common.truth.Truth.assertThat
import org.bit63.albumarchiver.data.PageSize
import org.junit.After
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/** Requirements 11 and 12: reviewing and deleting shots and pages. */
@RunWith(AndroidJUnit4::class)
class ReviewTest {
    @get:Rule val compose = createEmptyComposeRule()
    @get:Rule val camera = GrantPermissionRule.grant(Manifest.permission.CAMERA)

    private val container = TestContainer(app)
    private val repo = container.repository
    private lateinit var albumId: String

    @After fun tearDown() = closeApp()

    /** Pages of 2, 3 and 1 shots; the current page is page 3. */
    @Before fun setUp() {
        albumId = io {
            val a = repo.createAlbum("Review", PageSize.DEFAULT)
            listOf(2, 3, 1).forEachIndexed { i, n ->
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

    private fun labelIs(text: String) = compose.waitUntil(5000) {
        runCatching { compose.onNodeWithTag("reviewLabel").assertTextContains(text) }.isSuccess
    }

    private fun openOverview() {
        launch(container)
        compose.waitForTag("pageNumber")
        compose.onNodeWithTag("pageNumber").performClick()
        compose.waitForTag("pageGrid")
    }

    @Test fun theOverviewShowsEveryPageWithItsShotCount() {
        openOverview()
        compose.onNodeWithTag("page:1").assertExists()
        compose.onNodeWithTag("page:2").assertExists()
        compose.onNodeWithTag("page:3").assertExists()
        compose.onNodeWithText("3 shots").assertExists()
        compose.onNodeWithText("1 shot").assertExists()
    }

    @Test fun aThumbnailOpensTheReviewAtThatShot() {
        launch(container)
        compose.waitForTag("thumb")
        compose.onAllNodesWithTag("thumb").onFirst().performClick()
        labelIs("Page 3, shot 1 of 1")
    }

    @Test fun swipingMovesThroughTheShotsOfAPage() {
        openOverview()
        compose.onNodeWithTag("page:2").performClick()
        labelIs("Page 2, shot 1 of 3")
        compose.onNodeWithTag("pager").performTouchInput { swipeLeft() }
        labelIs("Page 2, shot 2 of 3")
        compose.onNodeWithTag("pager").performTouchInput { swipeLeft() }
        labelIs("Page 2, shot 3 of 3")
        compose.onNodeWithTag("pager").performTouchInput { swipeLeft() }
        labelIs("Page 2, shot 3 of 3")
        compose.onNodeWithTag("pager").performTouchInput { swipeRight() }
        labelIs("Page 2, shot 2 of 3")
    }

    @Test fun pageArrowsMoveBetweenPagesAndHideAtTheEnds() {
        openOverview()
        compose.onNodeWithTag("page:1").performClick()
        labelIs("Page 1, shot 1 of 2")
        assertThat(compose.onAllNodesWithTag("previousPageArrow").fetchSemanticsNodes()).isEmpty()
        compose.onNodeWithTag("nextPageArrow").performClick()
        labelIs("Page 2, shot 1 of 3")
        compose.onNodeWithTag("nextPageArrow").performClick()
        labelIs("Page 3, shot 1 of 1")
        assertThat(compose.onAllNodesWithTag("nextPageArrow").fetchSemanticsNodes()).isEmpty()
        compose.onNodeWithTag("previousPageArrow").performClick()
        labelIs("Page 2, shot 1 of 3")
    }

    @Test fun deletingAShotAsksAndShowsTheNextShot() {
        openOverview()
        compose.onNodeWithTag("page:2").performClick()
        labelIs("Page 2, shot 1 of 3")
        compose.onNodeWithTag("reviewMenu").performClick()
        compose.onNodeWithText("Delete shot").performClick()
        compose.waitForText("Delete this shot of page 2?")
        compose.onNodeWithTag("confirmDelete").performClick()
        labelIs("Page 2, shot 1 of 2")
        assertThat(io { repo.shot("p2s1") }).isNull()
    }

    @Test fun deletingAnInnerPagesOnlyShotSaysThePageGoesToo() {
        io { repo.deleteShot("p1s1") }
        openOverview()
        compose.onNodeWithTag("page:1").performClick()
        labelIs("Page 1, shot 1 of 1")
        compose.onNodeWithTag("reviewMenu").performClick()
        compose.onNodeWithText("Delete shot").performClick()
        compose.waitForText("so the page is deleted too", substring = true)
        compose.onNodeWithTag("confirmDelete").performClick()
        labelIs("Page 1, shot 1 of 3")
        assertThat(io { repo.pages(albumId) }.map { it.position }).containsExactly(1, 2).inOrder()
    }

    @Test fun deletingAPageFromReviewRenumbers() {
        openOverview()
        compose.onNodeWithTag("page:1").performClick()
        labelIs("Page 1, shot 1 of 2")
        compose.onNodeWithTag("reviewMenu").performClick()
        compose.onNodeWithText("Delete page").performClick()
        compose.waitForText("Delete page 1?")
        compose.onNodeWithText("2 shots", substring = true).assertExists()
        compose.onNodeWithTag("confirmDelete").performClick()
        labelIs("Page 1, shot 1 of 3")
        assertThat(io { repo.pages(albumId) }).hasSize(2)
    }

    @Test fun longPressInTheOverviewDeletesAPage() {
        openOverview()
        compose.onNodeWithTag("page:2").performTouchInput { longClick() }
        compose.waitForText("Delete page 2?")
        compose.onNodeWithTag("confirmDelete").performClick()
        compose.waitUntil(5000) { io { repo.pages(albumId).size } == 2 }
        compose.waitUntil(5000) { compose.onAllNodesWithTag("page:3").fetchSemanticsNodes().isEmpty() }
    }

    @Test fun deletingEverythingReturnsToTheOverview() {
        io {
            repo.pages(albumId).dropLast(1).forEach { repo.deletePage(it.id) }
        }
        openOverview()
        compose.onNodeWithTag("page:1").performClick()
        labelIs("Page 1, shot 1 of 1")
        compose.onNodeWithTag("reviewMenu").performClick()
        compose.onNodeWithText("Delete page").performClick()
        compose.onNodeWithTag("confirmDelete").performClick()
        compose.waitForTag("noPages")
    }

    @Test fun reviewingNeverChangesWhereNewShotsGo() {
        openOverview()
        compose.onNodeWithTag("page:1").performClick()
        labelIs("Page 1, shot 1 of 2")
        dismissSystemDialogs()
        androidx.test.platform.app.InstrumentationRegistry.getInstrumentation().sendKeyDownUpSync(android.view.KeyEvent.KEYCODE_BACK)
        compose.waitForTag("pageGrid")
        androidx.test.platform.app.InstrumentationRegistry.getInstrumentation().sendKeyDownUpSync(android.view.KeyEvent.KEYCODE_BACK)
        compose.waitForIdle()
        compose.waitForTag("shutter")
        compose.onNodeWithTag("pageNumber").assertTextContains("Page 3")
        compose.onNodeWithTag("shutter").performClick()
        compose.waitUntil(5000) { io { repo.lastPage(albumId)!!.shotCount } == 2 }
        assertThat(io { repo.pages(albumId).first().shotCount }).isEqualTo(2)
    }

    @Test fun theDrawerOpensTheOverview() {
        launch(container)
        compose.waitForTag("menu")
        compose.onNodeWithTag("menu").performClick()
        compose.waitForTag("drawer")
        compose.onNode(androidx.compose.ui.test.hasContentDescription("Pages of Review")).performClick()
        compose.waitForTag("pageGrid")
        compose.onAllNodesWithTag("page:3").onLast().assertExists()
    }
}
