package org.bit63.albumarchiver

import android.Manifest
import androidx.compose.ui.semantics.getOrNull
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.assertTextContains
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.rule.GrantPermissionRule
import com.google.common.truth.Truth.assertThat
import org.bit63.albumarchiver.data.Limits
import org.bit63.albumarchiver.data.Page
import org.bit63.albumarchiver.data.PageSize
import org.bit63.albumarchiver.testing.FakeCamera
import org.junit.After
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import java.io.IOException

@RunWith(AndroidJUnit4::class)
class CaptureScreenTest {
    @get:Rule val compose = createEmptyComposeRule()
    @get:Rule val camera = GrantPermissionRule.grant(Manifest.permission.CAMERA)

    private val fakeCamera = FakeCamera(::realJpeg)
    private val container = TestContainer(app, camera = fakeCamera)
    private val repo = container.repository

    @After fun tearDown() = closeApp()

    private fun withAlbum(name: String = "Family"): String = io {
        val a = repo.createAlbum(name, PageSize.DEFAULT)
        container.testSettings.setLastAlbumId(a.id)
        a.id
    }

    private fun shotCountIs(n: Int) {
        val label = if (n == 1) "1 shot" else "$n shots"
        compose.waitUntil(5000) {
            compose.onAllNodesWithTag("shotCount").fetchSemanticsNodes().any { node ->
                node.config.getOrNull(androidx.compose.ui.semantics.SemanticsProperties.Text)?.any { it.text.contains(label) } == true
            }
        }
    }

    @Test fun launchWithNoAlbumShowsTheEmptyState() {
        launch(container)
        compose.waitForTag("emptyState")
        compose.onNodeWithTag("newAlbum").assertIsDisplayed()
        compose.onAllNodesWithTag("shutter").assertCountEquals0()
    }

    @Test fun launchOpensTheLastAlbumAtItsLastPage() {
        val id = withAlbum("Rawlins 1962")
        io {
            repo.addShot(id, "s1", container.shotStore.finish(container.shotStore.newTempFile("s1").apply { writeBytes(realJpeg("s1")) }))
            repo.startNextPage(id)
        }
        launch(container)
        compose.waitForText("Rawlins 1962")
        compose.onNodeWithTag("pageNumber").assertTextContains("Page 2")
        shotCountIs(0)
    }

    @Test fun aDeletedLastAlbumFallsBackToTheEmptyState() {
        io { container.testSettings.setLastAlbumId("gone") }
        launch(container)
        compose.waitForTag("emptyState")
    }

    @Test fun theShutterAddsAShotWithAThumbnail() {
        val id = withAlbum()
        launch(container)
        compose.waitForTag("shutter")
        compose.onNodeWithTag("shutter").performClick()
        shotCountIs(1)
        compose.onNodeWithTag("pageNumber").assertTextContains("Page 1")
        compose.waitForTag("thumb")
        compose.onNodeWithTag("shutter").performClick()
        shotCountIs(2)
        assertThat(compose.onAllNodesWithTag("thumb").fetchSemanticsNodes()).hasSize(2)
        assertThat(io { repo.pages(id) }).hasSize(1)
    }

    @Test fun nextPageOnAnEmptyPageShowsAHint() {
        val id = withAlbum()
        launch(container)
        compose.waitForTag("nextPage")
        compose.onNodeWithTag("nextPage").performClick()
        compose.waitForText("This page has no shots yet")
        assertThat(io { repo.pages(id) }).isEmpty()
    }

    @Test fun nextPageShowsTheNewNumberAndUndoRemovesIt() {
        val id = withAlbum()
        launch(container)
        compose.waitForTag("shutter")
        compose.onNodeWithTag("shutter").performClick()
        shotCountIs(1)
        compose.mainClock.autoAdvance = true
        compose.onNodeWithTag("nextPage").performClick()
        compose.waitForTag("bigPageNumber")
        compose.onNodeWithTag("bigPageNumber").assertTextContains("Page 2")
        compose.onNodeWithTag("pageNumber").assertTextContains("Page 2")
        compose.waitForText("Undo")
        compose.onNodeWithText("Undo").performClick()
        compose.waitUntil(5000) { io { repo.pages(id).size } == 1 }
        compose.waitUntil(5000) {
            runCatching { compose.onNodeWithTag("pageNumber").assertTextContains("Page 1") }.isSuccess
        }
    }

    @Test fun theNewPageNumberDisappearsAfterAboutASecond() {
        withAlbum()
        launch(container)
        compose.waitForTag("shutter")
        compose.onNodeWithTag("shutter").performClick()
        shotCountIs(1)
        compose.onNodeWithTag("nextPage").performClick()
        compose.waitForTag("bigPageNumber")
        compose.waitUntil(3000) { compose.onAllNodesWithTag("bigPageNumber").fetchSemanticsNodes().isEmpty() }
    }

    @Test fun aFullPageDisablesTheShutterAndSaysSo() {
        val id = withAlbum()
        io {
            repeat(Limits.MAX_SHOTS_PER_PAGE) {
                val t = container.shotStore.newTempFile("s$it").apply { writeBytes(realJpeg("s$it")) }
                repo.addShot(id, "s$it", container.shotStore.finish(t))
            }
        }
        launch(container)
        compose.waitForTag("limitHint")
        compose.onNodeWithTag("limitHint").assertTextContains("Page full", substring = true)
        compose.onNodeWithTag("shutter").assertIsNotEnabled()
        compose.onNodeWithTag("nextPage").assertIsEnabled()
    }

    @Test fun aFullAlbumDisablesNextPageAndSaysSo() {
        val id = withAlbum()
        io { container.db.pages().insertAll((1..Limits.MAX_PAGES_PER_ALBUM).map { Page("p$it", id, it, shotCount = 1) }) }
        launch(container)
        compose.waitForTag("limitHint")
        compose.onNodeWithTag("limitHint").assertTextContains("Album full", substring = true)
        compose.onNodeWithTag("nextPage").assertIsNotEnabled()
    }

    @Test fun aCameraErrorIsShownAndNoShotIsAdded() {
        val id = withAlbum()
        fakeCamera.fail = IOException("sensor busy")
        launch(container)
        compose.waitForTag("shutter")
        compose.onNodeWithTag("shutter").performClick()
        compose.waitForText("sensor busy", substring = true)
        assertThat(io { repo.pages(id) }).isEmpty()
    }

    @Test fun flashStartsOffAndToggles() {
        withAlbum()
        launch(container)
        compose.waitForTag("flash")
        compose.onNodeWithContentDescription("Flash off").assertIsDisplayed()
        compose.onNodeWithTag("flash").performClick()
        compose.onNodeWithContentDescription("Flash on").assertIsDisplayed()
        assertThat(fakeCamera.flashOn).isTrue()
    }

    @Test fun lowStorageAndNoServerBanners() {
        freeBytes.set(100L * 1024 * 1024)
        try {
            withAlbum()
            launch(container)
            compose.waitForTag("lowStorage")
            compose.waitForTag("noServer")
        } finally {
            freeBytes.set(10L * 1024 * 1024 * 1024)
        }
    }

    @Test fun rejectedTokenBannerPointsToSettings() {
        withAlbum()
        io {
            container.testSettings.setServer("http://127.0.0.1:1", "t")
            container.testSettings.setAuthRejected(true)
        }
        launch(container)
        compose.waitForTag("authRejected")
        compose.onNodeWithTag("bannerAction").performClick()
        compose.waitForTag("serverUrl")
    }

    @Test fun pendingUploadsAreCounted() {
        withAlbum()
        launch(container)
        compose.waitForTag("shutter")
        compose.onNodeWithTag("shutter").performClick()
        compose.waitForTag("pendingUploads")
        compose.onNodeWithTag("pendingUploads", useUnmergedTree = true).assertExists()
    }

    @Test fun theScreenStaysOnWhileCapturing() {
        withAlbum()
        val scenario = launch(container)
        compose.waitForTag("shutter")
        var keepOn = false
        scenario.onActivity { keepOn = it.window.decorView.findViewById<android.view.View>(android.R.id.content).rootView.keepScreenOn }
        // keepScreenOn is set on the compose view, which sits under the content root.
        var anyKeepOn = false
        scenario.onActivity { activity ->
            fun walk(v: android.view.View) {
                if (v.keepScreenOn) anyKeepOn = true
                if (v is android.view.ViewGroup) for (i in 0 until v.childCount) walk(v.getChildAt(i))
            }
            walk(activity.window.decorView)
        }
        assertThat(anyKeepOn || keepOn).isTrue()
    }
}

private fun androidx.compose.ui.test.SemanticsNodeInteractionCollection.assertCountEquals0() =
    assertThat(fetchSemanticsNodes()).isEmpty()

private fun androidx.compose.ui.test.junit4.ComposeTestRule.onNodeWithContentDescription(text: String) =
    onNode(androidx.compose.ui.test.hasContentDescription(text))
