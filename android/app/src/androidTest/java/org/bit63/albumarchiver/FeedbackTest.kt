package org.bit63.albumarchiver

import android.Manifest
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.assertTextContains
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.rule.GrantPermissionRule
import com.google.common.truth.Truth.assertThat
import org.bit63.albumarchiver.data.PageFeedback
import org.bit63.albumarchiver.data.PageSize
import org.junit.After
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import java.time.Instant

/**
 * Overlap coverage spec, Requirement 4: a warned page's red dot, the photo
 * with its warnings and rectangles, and adding shots to that page.
 */
@RunWith(AndroidJUnit4::class)
class FeedbackTest {
    @get:Rule val compose = createEmptyComposeRule()
    @get:Rule val camera = GrantPermissionRule.grant(Manifest.permission.CAMERA)

    private val container = TestContainer(app)
    private val repo = container.repository
    private lateinit var albumId: String
    private lateinit var page1: String

    @After fun tearDown() = closeApp()

    /** Two pages of two shots, all "uploaded"; page 1's feedback says only one photo covers its right part, shown in shot p1s2. */
    @Before fun setUp() {
        albumId = io {
            val a = repo.createAlbum("Feedback", PageSize.DEFAULT)
            listOf(2, 2).forEachIndexed { i, n ->
                if (i > 0) repo.startNextPage(a.id)
                repeat(n) { j ->
                    val id = "p${i + 1}s${j + 1}"
                    val t = container.shotStore.newTempFile(id).apply { writeBytes(realJpeg(id)) }
                    repo.addShot(a.id, id, container.shotStore.finish(t))
                }
            }
            container.db.ops().all().forEach { container.db.ops().delete(it.seq) }
            page1 = repo.pages(a.id).first().id
            repo.saveFeedback(
                listOf(
                    PageFeedback(
                        page1, a.id, "done", "2026-10-14T01:00:00Z",
                        """[{"code":"single_coverage","fraction":0.31,"locations":["right"],
                            "regions":[{"bbox":[0.62,0,1,1],"location":"right","shots":[1],"shot_boxes":{"p1s2":[40,0,63,47]}}],
                            "message":"Only one photo covers the right edge (31.0% of the page). Take another shot of that part from a different angle so that glare there can be removed."}]""",
                        Instant.now(),
                    ),
                ),
            )
            container.testSettings.setLastAlbumId(a.id)
            a.id
        }
    }

    @Test fun aWarnedPageShowsARedDotAndItsPhotoTheWarningAndTheArea() {
        launch(container)
        compose.waitForTag("pageNumber")
        compose.onAllNodesWithTag("pageWarningDot").assertCountEquals(0)  // page 2, the current page, is fine
        compose.onNodeWithTag("pageNumber").performClick()
        compose.waitForTag("pageGrid")
        compose.waitForTag("pageDot:1")
        compose.onAllNodesWithTag("pageDot:2").assertCountEquals(0)
        compose.onNodeWithTag("page:1").performClick()
        compose.waitForTag("toShot")  // the page view first (no preview yet, so the first photo stands in)
        compose.onNodeWithTag("toShot").performClick()
        compose.waitForTag("warningsBar")
        // Opened at the shot that shows the area, with the rectangle on it and the message under it.
        compose.onNodeWithTag("reviewLabel").assertTextContains("Page 1, shot 2 of 2")
        compose.waitForTag("warningRects")
        compose.onNodeWithText("Only one photo covers the right edge", substring = true).assertExists()
        compose.onAllNodesWithTag("thumbWarningDot", useUnmergedTree = true).assertCountEquals(2)
    }

    @Test fun addShotsToThePageMakesItTheCaptureTargetAndBackReturns() {
        launch(container)
        compose.waitForTag("pageNumber")
        compose.onNodeWithTag("pageNumber").performClick()
        compose.waitForTag("pageDot:1")
        compose.onNodeWithTag("page:1").performClick()
        compose.waitForTag("addShots")  // the page view's own warnings bar
        compose.onNodeWithTag("addShots").performClick()
        compose.waitForTag("shutter")
        compose.waitUntil(5000) { runCatching { compose.onNodeWithTag("pageNumber").assertTextContains("Page 1") }.isSuccess }
        compose.onNodeWithTag("nextPage").assertTextContains("Back to page 2")
        compose.waitForTag("pageWarningDot")
        compose.onNodeWithTag("shutter").performClick()
        compose.waitUntil(5000) { io { repo.shots(page1) }.size == 3 }
        assertThat(io { repo.lastPage(albumId) }!!.shotCount).isEqualTo(2)
        compose.onNodeWithTag("nextPage").performClick()
        compose.waitUntil(5000) { runCatching { compose.onNodeWithTag("pageNumber").assertTextContains("Page 2") }.isSuccess }
        compose.onNodeWithTag("nextPage").assertTextContains("Next page")
        assertThat(io { repo.pages(albumId) }).hasSize(2)
    }
}
