package org.bit63.albumarchiver

import android.Manifest
import androidx.compose.ui.test.assertTextContains
import androidx.compose.ui.test.click
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTouchInput
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.rule.GrantPermissionRule
import org.bit63.albumarchiver.data.PageFeedback
import org.bit63.albumarchiver.data.PageSize
import org.junit.After
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import java.time.Instant

/** Overlap coverage spec, Requirement 6: the composed page with each shot's outline, and the way to the shot. */
@RunWith(AndroidJUnit4::class)
class PageViewTest {
    @get:Rule val compose = createEmptyComposeRule()
    @get:Rule val camera = GrantPermissionRule.grant(Manifest.permission.CAMERA)

    private val container = TestContainer(app)
    private val repo = container.repository
    private lateinit var albumId: String
    private lateinit var pageId: String

    @After fun tearDown() = closeApp()

    /** One page of two uploaded shots, with a preview already in the cache and feedback that says the right part is single-covered. */
    @Before fun setUp() {
        albumId = io {
            val a = repo.createAlbum("Pages", PageSize.DEFAULT)
            for (id in listOf("s1", "s2")) {
                val t = container.shotStore.newTempFile(id).apply { writeBytes(realJpeg(id)) }
                repo.addShot(a.id, id, container.shotStore.finish(t))
            }
            container.db.ops().all().forEach { container.db.ops().delete(it.seq) }
            pageId = repo.lastPage(a.id)!!.id
            val previews = File(container.context.cacheDir, "previews").apply { mkdirs() }
            File(previews, "$pageId.jpg").writeBytes(realJpeg("preview"))
            File(previews, "$pageId.run").writeText("run1")
            repo.saveFeedback(
                listOf(
                    PageFeedback(
                        pageId, a.id, "done", "run1",
                        """[{"code":"single_coverage","fraction":0.31,"locations":["right"],
                            "regions":[{"bbox":[0.62,0,1,1],"location":"right","shots":[1],"shot_boxes":{"s2":[40,0,63,47]}}],
                            "message":"Only one photo covers the right edge (31.0% of the page)."}]""",
                        Instant.now(),
                        preview = true,
                        outlinesJson = """{"s1":[[0,0],[0.65,0],[0.65,1],[0,1]],"s2":[[0.35,0],[1,0],[1,1],[0.35,1]]}""",
                    ),
                ),
            )
            container.testSettings.setLastAlbumId(a.id)
            a.id
        }
    }

    @Test fun thePageTileOpensTheComposedPageWithOutlinesAndTheShotToggleOpensTheReview() {
        launch(container)
        compose.waitForTag("pageNumber")
        compose.onNodeWithTag("pageNumber").performClick()
        compose.waitForTag("page:1")
        compose.onNodeWithTag("page:1").performClick()
        compose.waitForTag("pageOverlay")
        compose.onNodeWithTag("pageViewLabel").assertTextContains("Page 1")
        compose.waitForTag("warningsBar")
        // A tap on the right part, where only shot 2 lies and the single-coverage area is, opens that shot.
        compose.onNodeWithTag("pageImage").performTouchInput { click(percentOffset(0.9f, 0.5f)) }
        compose.waitForTag("reviewLabel")
        compose.onNodeWithTag("reviewLabel").assertTextContains("Page 1, shot 2 of 2")
        // And "Page" goes back to the page view.
        compose.onNodeWithTag("toPage").performClick()
        compose.waitForTag("pageOverlay")
        compose.onNodeWithTag("toShot").performClick()
        compose.waitForTag("reviewLabel")
    }
}
