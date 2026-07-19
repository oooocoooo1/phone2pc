package io.github.oooocoooo1.phone2pc

import android.content.Intent
import android.content.Context
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.os.Handler
import android.os.Looper
import android.provider.DocumentsContract
import android.provider.Settings
import android.util.Log
import java.io.BufferedInputStream
import java.io.BufferedOutputStream
import java.io.File
import java.io.InterruptedIOException
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.embedding.engine.FlutterEngineCache
import io.flutter.plugin.common.MethodChannel

class MainActivity : FlutterActivity() {
    private val channelName = "io.github.oooocoooo1.phone2pc/channel"
    private val fastChannelName = "io.github.oooocoooo1.phone2pc/fast_transfer"
    private val uploadExecutor = Executors.newSingleThreadExecutor()
    private val uploadRunning = AtomicBoolean(false)
    private val uploadCancelled = AtomicBoolean(false)
    private val mainHandler = Handler(Looper.getMainLooper())
    @Volatile private var uploadConnection: HttpURLConnection? = null

    override fun provideFlutterEngine(context: Context): FlutterEngine? =
        FlutterEngineCache.getInstance().get(Phone2PCApplication.ENGINE_ID)

    override fun shouldDestroyEngineWithHost(): Boolean = false

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, channelName).setMethodCallHandler { call, result ->
            when (call.method) {
                "minimize" -> {
                    moveTaskToBack(true)
                    result.success(null)
                }
                "getSdkInt" -> result.success(Build.VERSION.SDK_INT)
                "openAppSettings" -> {
                    val intent = Intent(
                        Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                        Uri.parse("package:$packageName"),
                    )
                    startActivity(intent)
                    result.success(true)
                }
                "getPublicDownloadDirectory" -> {
                    @Suppress("DEPRECATION")
                    val downloads = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS)
                    result.success(downloads.absolutePath)
                }
                "openDirectory" -> {
                    val path = call.argument<String>("path")
                    if (path.isNullOrBlank()) {
                        result.error("INVALID_PATH", "目录路径为空", null)
                    } else {
                        result.success(openDirectory(File(path)))
                    }
                }
                "openFileUri" -> {
                    val uri = call.argument<String>("uri")
                    val name = call.argument<String>("name")
                    result.success(openFileUri(uri, name))
                }
                "persistUriPermission" -> {
                    val uri = call.argument<String>("uri")
                    result.success(persistUriPermission(uri))
                }
                "openParentDirectory" -> {
                    val path = call.argument<String>("path")
                    val uri = call.argument<String>("uri")
                    result.success(openParentDirectory(path, uri))
                }
                else -> result.notImplemented()
            }
        }

        val fastChannel = MethodChannel(flutterEngine.dartExecutor.binaryMessenger, fastChannelName)
        fastChannel.setMethodCallHandler { call, result ->
            when (call.method) {
                "uploadFile" -> startFastUpload(call.arguments as? Map<*, *>, result, fastChannel)
                "cancelUpload" -> {
                    uploadCancelled.set(true)
                    uploadConnection?.disconnect()
                    result.success(null)
                }
                else -> result.notImplemented()
            }
        }
    }

    override fun onDestroy() {
        uploadCancelled.set(true)
        uploadConnection?.disconnect()
        uploadExecutor.shutdownNow()
        super.onDestroy()
    }

    private fun startFastUpload(
        arguments: Map<*, *>?,
        result: MethodChannel.Result,
        channel: MethodChannel,
    ) {
        val path = arguments?.get("path") as? String
        val host = arguments?.get("host") as? String
        val fileId = arguments?.get("file_id") as? String
        val token = arguments?.get("token") as? String
        val port = arguments?.get("port") as? Int
        val declaredSize = arguments?.get("size") as? Long
            ?: (arguments?.get("size") as? Int)?.toLong()
        if (path.isNullOrBlank() || host.isNullOrBlank() || fileId.isNullOrBlank() ||
            token.isNullOrBlank() || port == null || port !in 1..65535 || declaredSize == null
        ) {
            result.error("INVALID_ARGUMENTS", "HTTP 高速传输参数无效", null)
            return
        }
        if (!uploadRunning.compareAndSet(false, true)) {
            result.error("UPLOAD_BUSY", "已有文件正在发送", null)
            return
        }

        uploadCancelled.set(false)
        uploadExecutor.execute {
            try {
                val response = uploadFile(path, host, port, fileId, token, declaredSize, channel)
                mainHandler.post { result.success(response) }
            } catch (error: Exception) {
                val message = error.message ?: error.javaClass.simpleName
                mainHandler.post { result.error("UPLOAD_FAILED", message, null) }
            } finally {
                uploadConnection = null
                uploadRunning.set(false)
            }
        }
    }

    private fun uploadFile(
        path: String,
        host: String,
        port: Int,
        fileId: String,
        token: String,
        declaredSize: Long,
        channel: MethodChannel,
    ): Map<String, Any> {
        val file = File(path)
        require(file.isFile) { "文件不存在" }
        require(file.length() == declaredSize) { "文件大小已发生变化" }

        val urlHost = if (host.contains(':') && !host.startsWith('[')) "[$host]" else host
        val connection = URL("http://$urlHost:$port/phone2pc/upload").openConnection() as HttpURLConnection
        uploadConnection = connection
        connection.requestMethod = "POST"
        connection.doOutput = true
        connection.useCaches = false
        connection.connectTimeout = 10_000
        connection.readTimeout = 60_000
        connection.setFixedLengthStreamingMode(declaredSize)
        connection.setRequestProperty("Content-Type", "application/octet-stream")
        connection.setRequestProperty("X-Phone2PC-File-ID", fileId)
        connection.setRequestProperty("X-Phone2PC-Token", token)
        connection.setRequestProperty("Connection", "close")

        val digest = MessageDigest.getInstance("SHA-256")
        val buffer = ByteArray(1024 * 1024)
        var transferred = 0L
        var lastProgressAt = 0L
        try {
            BufferedInputStream(file.inputStream(), buffer.size).use { input ->
                BufferedOutputStream(connection.outputStream, buffer.size).use { output ->
                    while (true) {
                        if (uploadCancelled.get()) throw InterruptedIOException("发送已取消")
                        val count = input.read(buffer)
                        if (count < 0) break
                        output.write(buffer, 0, count)
                        digest.update(buffer, 0, count)
                        transferred += count
                        val now = System.nanoTime()
                        if (lastProgressAt == 0L || now - lastProgressAt >= 250_000_000L || transferred == declaredSize) {
                            lastProgressAt = now
                            val progress = mapOf(
                                "file_id" to fileId,
                                "transferred" to transferred,
                                "total" to declaredSize,
                            )
                            mainHandler.post { channel.invokeMethod("progress", progress) }
                        }
                    }
                    output.flush()
                }
            }

            val status = connection.responseCode
            if (status != HttpURLConnection.HTTP_OK) {
                throw IllegalStateException("HTTP 高速传输失败 ($status)")
            }
            return mapOf(
                "transferred" to transferred,
                "sha256" to digest.digest().joinToString("") { "%02x".format(it) },
            )
        } finally {
            connection.disconnect()
        }
    }

    private fun openDirectory(directory: File): Boolean {
        directory.mkdirs()
        val documentId = toPrimaryDocumentId(directory)
        val uri = DocumentsContract.buildDocumentUri(
            "com.android.externalstorage.documents",
            documentId,
        )

        return openDocumentDirectory(uri)
    }

    private fun openDocumentDirectory(uri: Uri): Boolean {
        // Resolve Android's DocumentsUI package first.  A generic directory
        // ACTION_VIEW is also claimed by office suites, browsers and cloud
        // drives, which produces an unwanted "Open with" chooser.
        val pickerIntent = Intent(Intent.ACTION_OPEN_DOCUMENT_TREE)
        @Suppress("DEPRECATION")
        val documentsUiPackage = packageManager.resolveActivity(
            pickerIntent,
            PackageManager.MATCH_DEFAULT_ONLY,
        )?.activityInfo?.packageName

        val viewIntent = Intent(Intent.ACTION_VIEW).apply {
            setDataAndType(uri, DocumentsContract.Document.MIME_TYPE_DIR)
            if (!documentsUiPackage.isNullOrBlank()) {
                setPackage(documentsUiPackage)
            }
        }
        try {
            startActivity(viewIntent)
            return true
        } catch (error: RuntimeException) {
            // Some vendor builds expose DocumentsUI only through the picker.
            Log.w("Phone2PC", "Direct directory view unavailable", error)
        }

        val treeIntent = pickerIntent.apply {
            putExtra(DocumentsContract.EXTRA_INITIAL_URI, uri)
            if (!documentsUiPackage.isNullOrBlank()) {
                setPackage(documentsUiPackage)
            }
            addFlags(
                Intent.FLAG_GRANT_READ_URI_PERMISSION or
                    Intent.FLAG_GRANT_WRITE_URI_PERMISSION or
                    Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION,
            )
        }
        return try {
            startActivity(treeIntent)
            true
        } catch (error: RuntimeException) {
            Log.e("Phone2PC", "No system file manager can open the directory", error)
            false
        }
    }

    private fun openFileUri(rawUri: String?, name: String?): Boolean {
        if (rawUri.isNullOrBlank()) return false
        return try {
            val uri = Uri.parse(rawUri)
            val mime = contentResolver.getType(uri)
                ?: java.net.URLConnection.guessContentTypeFromName(name)
                ?: "*/*"
            val intent = Intent(Intent.ACTION_VIEW).apply {
                setDataAndType(uri, mime)
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            }
            startActivity(intent)
            true
        } catch (error: RuntimeException) {
            Log.w("Phone2PC", "Unable to open source URI", error)
            false
        }
    }

    private fun persistUriPermission(rawUri: String?): Boolean {
        if (rawUri.isNullOrBlank()) return false
        return try {
            contentResolver.takePersistableUriPermission(
                Uri.parse(rawUri),
                Intent.FLAG_GRANT_READ_URI_PERMISSION,
            )
            true
        } catch (error: RuntimeException) {
            Log.w("Phone2PC", "Source URI does not support persistent access", error)
            false
        }
    }

    private fun openParentDirectory(path: String?, rawUri: String?): Boolean {
        // FilePicker exposes the original SAF URI on Android.  External
        // storage document IDs retain the relative path, so their exact
        // parent can be opened without exposing the app's private cache.
        if (!rawUri.isNullOrBlank()) {
            try {
                val uri = Uri.parse(rawUri)
                if (
                    DocumentsContract.isDocumentUri(this, uri) &&
                    uri.authority == "com.android.externalstorage.documents"
                ) {
                    val documentId = DocumentsContract.getDocumentId(uri)
                    val separator = documentId.lastIndexOf('/')
                    val parentId = if (separator >= 0) {
                        documentId.substring(0, separator)
                    } else {
                        "primary:"
                    }
                    val parentUri = DocumentsContract.buildDocumentUri(
                        uri.authority,
                        parentId,
                    )
                    if (openDocumentDirectory(parentUri)) return true
                }
            } catch (error: RuntimeException) {
                Log.w("Phone2PC", "Unable to derive source directory", error)
            }
        }

        if (!path.isNullOrBlank()) {
            val file = File(path)
            val parent = file.parentFile
            @Suppress("DEPRECATION")
            val storageRoot = Environment.getExternalStorageDirectory().absolutePath
            if (parent != null && parent.absolutePath.startsWith(storageRoot)) {
                return openDirectory(parent)
            }
        }
        return false
    }

    private fun toPrimaryDocumentId(directory: File): String {
        @Suppress("DEPRECATION")
        val storageRoot = Environment.getExternalStorageDirectory().canonicalFile
        val target = directory.canonicalFile
        val relative = target.path.removePrefix(storageRoot.path).trimStart(File.separatorChar)
        return if (relative.isEmpty()) "primary:" else "primary:$relative"
    }
}
