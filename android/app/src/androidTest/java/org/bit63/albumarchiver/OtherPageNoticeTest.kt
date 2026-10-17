package org.bit63.albumarchiver

import android.Manifest
import androidx.compose.ui.test.assertTextContains
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performClick
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.rule.GrantPermissionRule
import org.bit63.albumarchiver.data.InMemorySettingsStore
import org.bit63.albumarchiver.data.PageSize
import org.bit63.albumarchiver.testing.FakeAlbumServer
import org.junit.After
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Single-shot glare spec, Requirement 3: while page 2 is being shot, the
 * server warns about page 1; the capture screen names it, and a tap opens
 * page 1's photo with the rectangle on it.
 */
@RunWith(AndroidJUnit4::class)
class OtherPageNoticeTest {
    @get:Rule val compose = createEmptyComposeRule()
    @get:Rule val camera = GrantPermissionRule.grant(Manifest.permission.CAMERA)

    private val fake = FakeAlbumServer().start()
    private val container = TestContainer(app, testSettings = InMemorySettingsStore(fake.url, fake.token))
    private val repo = container.repository

    @After fun tearDown() {
        closeApp()
        fake.shutdown()
    }

    /** Two pages of two shots, all uploaded; the server says only shot p1s2 covers page 1's right part. */
    @Before fun setUp() {
        io {
            val a = repo.createAlbum("Notice", PageSize.DEFAULT)
            listOf(2, 2).forEachIndexed { i, n ->
                if (i > 0) repo.startNextPage(a.id)
                repeat(n) { j ->
                    val id = "p${i + 1}s${j + 1}"
                    val t = container.shotStore.newTempFile(id).apply { writeBytes(realJpeg(id)) }
                    repo.addShot(a.id, id, container.shotStore.finish(t))
                }
            }
            container.db.ops().all().forEach { container.db.ops().delete(it.seq) }
            val (p1, p2) = repo.pages(a.id).map { it.id }
            fake.seed(a.id, a.name, "letter", listOf(emptyList(), emptyList()))
            fake.statusPages[a.id] = """[
                {"id":"$p1","number":1,"state":"done","last_run":"2026-10-17T01:00:00Z","warnings":[
                  {"code":"single_coverage","fraction":0.31,"locations":["right"],"glare_seen_regions":1,
                   "regions":[{"bbox":[0.62,0,1,1],"location":"right","shots":[1],"glare_seen":0.2,"shot_boxes":{"p1s2":[40,0,63,47]}}],
                   "message":"Only one photo covers the right edge, and glare was seen there (31.0% of the page)."}]},
                {"id":"$p2","number":2,"state":"done","last_run":"2026-10-17T01:00:00Z","warnings":[]}]"""
            container.testSettings.setLastAlbumId(a.id)
        }
    }

    @Test fun aWarningOnAnEarlierPageShowsTheNoticeAndATapOpensItsPhoto() {
        launch(container)
        compose.waitForTag("shutter")
        compose.onNodeWithTag("pageNumber").assertTextContains("Page 2")
        compose.waitForTag("otherPageNotice", timeoutMs = 15000)
        compose.onNodeWithTag("otherPageNotice").assertTextContains("Page 1 needs another shot")
        // The capture screen stays usable while it shows.
        compose.onNodeWithTag("shutter").assertExists()
        compose.onNodeWithTag("nextPage").assertExists()
        compose.onNodeWithTag("otherPageNotice").performClick()
        compose.waitForTag("reviewLabel")
        compose.onNodeWithTag("reviewLabel").assertTextContains("Page 1, shot 2 of 2")
        compose.waitForTag("warningRects")
    }
}
