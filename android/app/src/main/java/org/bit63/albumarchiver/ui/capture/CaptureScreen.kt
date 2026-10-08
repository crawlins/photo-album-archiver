package org.bit63.albumarchiver.ui.capture

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.provider.Settings
import android.view.HapticFeedbackConstants
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.view.PreviewView
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CloudUpload
import androidx.compose.material.icons.filled.FlashOff
import androidx.compose.material.icons.filled.FlashOn
import androidx.compose.material.icons.filled.Menu
import androidx.compose.material3.Button
import androidx.compose.material3.DrawerValue
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalNavigationDrawer
import androidx.compose.material3.SnackbarDuration
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.SnackbarResult
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberDrawerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LifecycleResumeEffect
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.compose.currentStateAsState
import androidx.lifecycle.viewmodel.compose.viewModel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull
import org.bit63.albumarchiver.camera.CameraController
import org.bit63.albumarchiver.data.AlbumSummary
import org.bit63.albumarchiver.ui.LocalContainer
import org.bit63.albumarchiver.ui.LocalVolumeKeys
import org.bit63.albumarchiver.ui.ShotActionsSheet
import org.bit63.albumarchiver.ui.ShotStrip
import org.bit63.albumarchiver.ui.VolumeKeyRouter
import org.bit63.albumarchiver.ui.albums.AlbumDialog
import org.bit63.albumarchiver.ui.albums.AlbumDrawer
import org.bit63.albumarchiver.ui.albums.AlbumForm
import org.bit63.albumarchiver.ui.albums.AlbumsViewModel
import org.bit63.albumarchiver.ui.albums.DeleteAlbumDialog
import java.time.LocalDate

/** Which dialog the capture screen is showing, if any. */
private sealed interface CaptureDialog {
    data object NewAlbum : CaptureDialog
    data class EditAlbum(val form: AlbumForm, val albumId: String) : CaptureDialog
    data class DeleteAlbum(val summary: AlbumSummary) : CaptureDialog
}

/**
 * The main view (Requirement 3): the camera preview with the shutter, "Next
 * page", the flash toggle, the album and page header, the current page's
 * thumbnails, upload and storage banners, and the album drawer.
 */
@Composable
fun CaptureScreen(
    openReview: (albumId: String, pageId: String, index: Int) -> Unit,
    openOverview: (albumId: String) -> Unit,
    openServerAlbums: () -> Unit,
    openSettings: () -> Unit,
) {
    val container = LocalContainer.current
    val vm: CaptureViewModel = viewModel {
        CaptureViewModel(container.repository, container.settings, container.shotStore, container.newCamera())
    }
    val albumsVm: AlbumsViewModel = viewModel {
        AlbumsViewModel(container.repository, container.settings, container.uploadRunning)
    }
    val state by vm.state.collectAsStateWithLifecycle()
    val flashOn by vm.flashOn.collectAsStateWithLifecycle()
    val lowStorage by vm.lowStorage.collectAsStateWithLifecycle()
    val capturing by vm.capturing.collectAsStateWithLifecycle()
    val albumRows by albumsVm.albums.collectAsStateWithLifecycle()
    val selectedShot by vm.shotActions.selected.collectAsStateWithLifecycle()
    val shotMenu by vm.shotActions.menu.collectAsStateWithLifecycle()

    val drawerState = rememberDrawerState(DrawerValue.Closed)
    val scope = rememberCoroutineScope()
    val snackbar = remember { SnackbarHostState() }
    val view = LocalView.current
    var dialog by remember { mutableStateOf<CaptureDialog?>(null) }
    var bigPageNumber by remember { mutableStateOf<Int?>(null) }
    val shutterFlash = remember { Animatable(0f) }

    // Keep the screen on while shooting (Requirement 3.6).
    DisposableEffect(view) {
        view.keepScreenOn = true
        onDispose { view.keepScreenOn = false }
    }

    // The volume keys belong to this screen only while it is resumed with an
    // album open and no drawer or dialog showing (Requirement 6.5).
    val lifecycleState by LocalLifecycleOwner.current.lifecycle.currentStateAsState()
    val volumeKeys = LocalVolumeKeys.current
    val keysActive = lifecycleState.isAtLeast(Lifecycle.State.RESUMED) &&
        state.album != null &&
        drawerState.currentValue == DrawerValue.Closed && drawerState.targetValue == DrawerValue.Closed &&
        dialog == null && shotMenu == null
    val currentVm by rememberUpdatedState(vm)
    DisposableEffect(keysActive, volumeKeys) {
        val handler = object : VolumeKeyRouter.Handler {
            override fun onVolumeUp() = currentVm.takeShot()
            override fun onVolumeDown() = currentVm.nextPage()
        }
        if (keysActive) volumeKeys.register(handler)
        onDispose { volumeKeys.unregister(handler) }
    }

    BackHandler(enabled = selectedShot != null) { vm.shotActions.clear() }

    LaunchedEffect(vm) {
        vm.events.collect { event ->
            when (event) {
                is CaptureEvent.ShotSaved -> {
                    // Brief visual and haptic feedback (Requirement 4.5).
                    view.performHapticFeedback(HapticFeedbackConstants.VIRTUAL_KEY)
                    launch {
                        shutterFlash.snapTo(0.7f)
                        shutterFlash.animateTo(0f, tween(250))
                    }
                }
                is CaptureEvent.PageStarted -> {
                    launch {
                        bigPageNumber = event.number
                        delay(1000)
                        bigPageNumber = null
                    }
                    launch {
                        // "Undo" for about five seconds (Requirement 5.4).
                        val result = withTimeoutOrNull(5000) {
                            snackbar.showSnackbar(
                                "Started page ${event.number}",
                                actionLabel = "Undo",
                                duration = SnackbarDuration.Indefinite,
                            )
                        }
                        if (result == null) snackbar.currentSnackbarData?.dismiss()
                        if (result == SnackbarResult.ActionPerformed) vm.undoNextPage(event.pageId)
                    }
                }
                is CaptureEvent.Message -> launch {
                    snackbar.currentSnackbarData?.dismiss()
                    snackbar.showSnackbar(event.text, duration = SnackbarDuration.Short)
                }
            }
        }
    }

    ModalNavigationDrawer(
        drawerState = drawerState,
        drawerContent = {
            AlbumDrawer(
                rows = albumRows,
                currentAlbumId = state.album?.id,
                onNewAlbum = {
                    scope.launch { drawerState.close() }
                    dialog = CaptureDialog.NewAlbum
                },
                onSelect = {
                    albumsVm.select(it)
                    scope.launch { drawerState.close() }
                },
                onOverview = {
                    scope.launch { drawerState.close() }
                    openOverview(it)
                },
                onEdit = { summary ->
                    scope.launch {
                        val album = albumsVm.album(summary.id) ?: return@launch
                        dialog = CaptureDialog.EditAlbum(AlbumForm.forEdit(album), album.id)
                    }
                },
                onDelete = { dialog = CaptureDialog.DeleteAlbum(it) },
                onServerAlbums = {
                    scope.launch { drawerState.close() }
                    openServerAlbums()
                },
                onSettings = {
                    scope.launch { drawerState.close() }
                    openSettings()
                },
            )
        },
    ) {
        Box(Modifier.fillMaxSize().background(Color.Black)) {
            CameraArea(vm, hasAlbum = state.album != null)

            Box(Modifier.fillMaxSize().alpha(shutterFlash.value).background(Color.White))

            // While a shot is selected, a tap on the preview only clears the selection.
            if (selectedShot != null) {
                Box(
                    Modifier.fillMaxSize()
                        .pointerInput(Unit) { detectTapGestures { vm.shotActions.clear() } }
                        .testTag("clearSelection"),
                )
            }

            Column(Modifier.fillMaxSize().statusBarsPadding().navigationBarsPadding()) {
                Header(
                    state = state,
                    flashOn = flashOn,
                    onMenu = { scope.launch { drawerState.open() } },
                    onFlash = vm::toggleFlash,
                    onPageNumber = { state.album?.let { openOverview(it.id) } },
                )
                Banners(
                    lowStorage = lowStorage,
                    hasAlbum = state.album != null,
                    serverConfigured = state.serverConfigured,
                    authRejected = state.authRejected,
                    openSettings = openSettings,
                )
                Spacer(Modifier.weight(1f))
                if (state.loaded && state.album == null) {
                    EmptyState(onNewAlbum = { dialog = CaptureDialog.NewAlbum })
                    Spacer(Modifier.weight(1f))
                } else if (state.album != null) {
                    LimitHint(state)
                    ShotStrip(
                        shots = state.shots,
                        selectedId = selectedShot,
                        onTap = { index, shot ->
                            val album = state.album
                            val page = state.page
                            if (!vm.shotActions.onTap(shot.id) && album != null && page != null) {
                                openReview(album.id, page.id, index)
                            }
                        },
                        onLongPress = { vm.shotActions.onLongPress(it.id) },
                        modifier = Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 4.dp).testTag("thumbnails"),
                    )
                    Controls(
                        shutterEnabled = !state.pageFull && !capturing,
                        nextEnabled = !state.albumFull,
                        onShutter = vm::takeShot,
                        onNextPage = vm::nextPage,
                    )
                }
            }

            AnimatedVisibility(
                visible = bigPageNumber != null,
                enter = fadeIn(),
                exit = fadeOut(),
                modifier = Modifier.align(Alignment.Center),
            ) {
                Text(
                    "Page ${bigPageNumber ?: ""}",
                    color = Color.White,
                    fontSize = 64.sp,
                    fontWeight = FontWeight.Bold,
                    modifier = Modifier
                        .background(Color(0x99000000), RoundedCornerShape(16.dp))
                        .padding(24.dp)
                        .testTag("bigPageNumber"),
                )
            }

            SnackbarHost(snackbar, Modifier.align(Alignment.BottomCenter).padding(bottom = 160.dp))
        }
    }

    shotMenu?.let { menu ->
        ShotActionsSheet(menu, onAction = vm::shotAction, onDismiss = vm.shotActions::closeMenu)
    }

    when (val d = dialog) {
        CaptureDialog.NewAlbum -> AlbumDialog(
            title = "New album",
            confirmLabel = "Create",
            initial = AlbumForm.forNew(LocalDate.now()),
            onConfirm = { form ->
                albumsVm.create(form)
                dialog = null
            },
            onDismiss = { dialog = null },
        )
        is CaptureDialog.EditAlbum -> AlbumDialog(
            title = "Edit album",
            confirmLabel = "Save",
            initial = d.form,
            onConfirm = { form ->
                albumsVm.update(d.albumId, form)
                dialog = null
            },
            onDismiss = { dialog = null },
        )
        is CaptureDialog.DeleteAlbum -> DeleteAlbumDialog(
            summary = d.summary,
            onConfirm = { alsoOnServer ->
                albumsVm.delete(d.summary.id, alsoOnServer)
                dialog = null
            },
            onDismiss = { dialog = null },
        )
        null -> Unit
    }
}

/** The live preview, or the camera permission rationale in its place (Requirement 3.7). */
@Composable
private fun CameraArea(vm: CaptureViewModel, hasAlbum: Boolean) {
    val context = LocalContext.current
    var granted by remember {
        mutableStateOf(
            ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED
        )
    }
    var asked by remember { mutableStateOf(false) }
    val launcher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {
        granted = it
        asked = true
    }
    // The permission can change outside the app, in system Settings, so it is
    // checked again whenever the screen comes back.
    LifecycleResumeEffect(Unit) {
        granted = ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED
        onPauseOrDispose {}
    }
    if (!granted) {
        val activity = context as? Activity
        val permanentlyDenied = asked && activity != null &&
            !ActivityCompat.shouldShowRequestPermissionRationale(activity, Manifest.permission.CAMERA)
        Column(
            Modifier.fillMaxSize().padding(32.dp).testTag("permissionRationale"),
            verticalArrangement = Arrangement.Center,
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text(
                "Album Archiver needs the camera to photograph your album pages. " +
                    "Photos stay on this phone until they reach your own server.",
                color = Color.White,
                textAlign = TextAlign.Center,
            )
            Spacer(Modifier.size(16.dp))
            if (permanentlyDenied) {
                Button(onClick = {
                    context.startActivity(
                        Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.fromParts("package", context.packageName, null))
                    )
                }) { Text("Open settings") }
            } else {
                Button(onClick = { launcher.launch(Manifest.permission.CAMERA) }) { Text("Grant camera access") }
            }
        }
        return
    }
    val camera = vm.camera
    if (camera is CameraController && hasAlbum) {
        val owner = LocalLifecycleOwner.current
        val previewView = remember { PreviewView(context).apply { scaleType = PreviewView.ScaleType.FIT_CENTER } }
        AndroidView(factory = { previewView }, modifier = Modifier.fillMaxSize().testTag("preview"))
        LaunchedEffect(owner) {
            camera.bind(owner, previewView).onFailure { vm.cameraUnavailable(it) }
        }
        DisposableEffect(owner) { onDispose { camera.unbind() } }
    } else if (hasAlbum) {
        // A test camera: no preview to show.
        Box(Modifier.fillMaxSize().background(Color(0xFF263238)).testTag("preview"))
    }
}

@Composable
private fun Header(
    state: CaptureState,
    flashOn: Boolean,
    onMenu: () -> Unit,
    onFlash: () -> Unit,
    onPageNumber: () -> Unit,
) {
    Row(
        Modifier.fillMaxWidth().background(Color(0x88000000)).padding(horizontal = 4.dp, vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        IconButton(onClick = onMenu, modifier = Modifier.testTag("menu")) {
            Icon(Icons.Default.Menu, contentDescription = "Albums", tint = Color.White)
        }
        val album = state.album
        Column(Modifier.weight(1f)) {
            Text(
                album?.name ?: "No album",
                color = Color.White,
                style = MaterialTheme.typography.titleMedium,
                maxLines = 1,
                modifier = Modifier.testTag("headerAlbumName"),
            )
            if (album != null) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        "Page ${state.pageNumber}",
                        color = Color.White,
                        fontWeight = FontWeight.Bold,
                        modifier = Modifier.clickable(onClick = onPageNumber).testTag("pageNumber"),
                    )
                    val shots = if (state.shotCount == 1) "1 shot" else "${state.shotCount} shots"
                    Text("  ·  $shots", color = Color.White, modifier = Modifier.testTag("shotCount"))
                }
            }
        }
        if (album != null && state.pendingUploads > 0) {
            Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.testTag("pendingUploads")) {
                Icon(Icons.Default.CloudUpload, contentDescription = null, tint = Color.White, modifier = Modifier.size(18.dp))
                Text(" ${state.pendingUploads}", color = Color.White)
            }
        }
        if (album != null) {
            IconButton(onClick = onFlash, modifier = Modifier.testTag("flash")) {
                Icon(
                    if (flashOn) Icons.Default.FlashOn else Icons.Default.FlashOff,
                    contentDescription = if (flashOn) "Flash on" else "Flash off",
                    tint = Color.White,
                )
            }
        }
    }
}

@Composable
private fun Banners(
    lowStorage: Boolean,
    hasAlbum: Boolean,
    serverConfigured: Boolean,
    authRejected: Boolean,
    openSettings: () -> Unit,
) {
    if (lowStorage) Banner("Storage is low: less than 500 MB free.", null, null, "lowStorage")
    if (hasAlbum && !serverConfigured) Banner("Uploads paused: no server set.", "Settings", openSettings, "noServer")
    if (serverConfigured && authRejected) {
        Banner("Uploads stopped: the server rejected the token.", "Settings", openSettings, "authRejected")
    }
}

@Composable
private fun Banner(text: String, action: String?, onAction: (() -> Unit)?, tag: String) {
    Surface(
        color = MaterialTheme.colorScheme.errorContainer,
        modifier = Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 2.dp).testTag(tag),
        shape = RoundedCornerShape(8.dp),
    ) {
        Row(Modifier.padding(horizontal = 12.dp, vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
            Text(text, modifier = Modifier.weight(1f), style = MaterialTheme.typography.bodySmall)
            if (action != null && onAction != null) {
                TextButton(onClick = onAction, modifier = Modifier.testTag("bannerAction")) { Text(action) }
            }
        }
    }
}

@Composable
private fun EmptyState(onNewAlbum: () -> Unit) {
    Column(
        Modifier.fillMaxWidth().padding(32.dp).testTag("emptyState"),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(
            "No album open. Create one to start photographing pages.",
            color = Color.White,
            textAlign = TextAlign.Center,
        )
        Spacer(Modifier.size(16.dp))
        Button(onClick = onNewAlbum, modifier = Modifier.testTag("newAlbum")) { Text("New album") }
    }
}

/** "Page full" and "Album full" hints (Requirements 4.10, 5.6). */
@Composable
private fun LimitHint(state: CaptureState) {
    val text = when {
        state.pageFull -> CaptureViewModel.PAGE_FULL
        state.albumFull -> CaptureViewModel.ALBUM_FULL
        else -> return
    }
    Text(
        text,
        color = Color.White,
        textAlign = TextAlign.Center,
        modifier = Modifier.fillMaxWidth().background(Color(0xAA000000)).padding(8.dp).testTag("limitHint"),
    )
}

@Composable
private fun Controls(
    shutterEnabled: Boolean,
    nextEnabled: Boolean,
    onShutter: () -> Unit,
    onNextPage: () -> Unit,
) {
    Row(
        Modifier.fillMaxWidth().padding(horizontal = 24.dp, vertical = 16.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(Modifier.weight(1f))
        Box(
            Modifier
                .size(84.dp)
                .clip(CircleShape)
                .border(4.dp, Color.White, CircleShape)
                .padding(8.dp)
                .clip(CircleShape)
                .background(if (shutterEnabled) Color.White else Color.Gray)
                .clickable(enabled = shutterEnabled, onClick = onShutter)
                .semantics { contentDescription = "Take shot" }
                .testTag("shutter"),
        )
        Box(Modifier.weight(1f), contentAlignment = Alignment.CenterEnd) {
            FilledTonalButton(
                onClick = onNextPage,
                enabled = nextEnabled,
                modifier = Modifier.width(110.dp).testTag("nextPage"),
            ) { Text("Next page") }
        }
    }
}
