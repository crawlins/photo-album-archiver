package org.bit63.albumarchiver.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import coil3.compose.AsyncImage
import coil3.request.ImageRequest
import org.bit63.albumarchiver.data.Shot
import org.bit63.albumarchiver.data.ShotState
import java.io.File

/**
 * A shot's thumbnail. Shots on the phone are decoded downsampled by Coil; a
 * shot of a server album not yet fetched shows the server's small preview,
 * or a placeholder while it loads or when it could not be loaded
 * (Requirements 14.4, 14.5, 14.9).
 */
@Composable
fun ShotThumbnail(shot: Shot, modifier: Modifier = Modifier) {
    if (shot.state == ShotState.PRESENT) {
        ShotFile(File(shot.path), modifier, ContentScale.Crop)
        return
    }
    val thumbs = LocalContainer.current.thumbFetcher
    var file by remember(shot.id) { mutableStateOf(thumbs.cached(shot.id)) }
    var failed by remember(shot.id) { mutableStateOf(false) }
    LaunchedEffect(shot.id) {
        if (file == null) {
            file = thumbs.fetch(shot)
            failed = file == null
        }
    }
    val f = file
    if (f != null) ShotFile(f, modifier, ContentScale.Crop) else Placeholder(if (failed) "Couldn't load" else "", modifier)
}

@Composable
fun ShotFile(file: File, modifier: Modifier = Modifier, scale: ContentScale = ContentScale.Fit) {
    AsyncImage(
        model = ImageRequest.Builder(LocalContext.current).data(file).build(),
        contentDescription = null,
        contentScale = scale,
        modifier = modifier,
    )
}

@Composable
fun Placeholder(text: String, modifier: Modifier = Modifier) {
    Box(modifier.background(Color(0xFF424242)), contentAlignment = Alignment.Center) {
        if (text.isNotEmpty()) {
            Text(
                text,
                color = Color.White,
                style = MaterialTheme.typography.labelSmall,
                textAlign = TextAlign.Center,
                modifier = Modifier.padding(4.dp),
            )
        }
    }
}

@Composable
fun FullPlaceholder(text: String) = Placeholder(text, Modifier.fillMaxSize())
