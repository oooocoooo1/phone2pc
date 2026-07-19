import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:math';

import 'package:crypto/crypto.dart';
import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:open_file/open_file.dart';
import 'package:permission_handler/permission_handler.dart';
import 'package:share_plus/share_plus.dart';
import 'package:shared_preferences/shared_preferences.dart';

const int _chunkSize = 1024 * 1024;
const int _ackWindow = 16 * 1024 * 1024;
const int _fastTransferPort = 8766;
const String _fastTransferPath = '/phone2pc/upload';
const int _maxFileSize = 10 * 1024 * 1024 * 1024;
const String _transferRecordsKey = 'transfer_records_v2';
const MethodChannel _platformChannel = MethodChannel(
  'io.github.oooocoooo1.phone2pc/channel',
);
const MethodChannel _fastTransferChannel = MethodChannel(
  'io.github.oooocoooo1.phone2pc/fast_transfer',
);

typedef SendJson = Future<void> Function(Map<String, dynamic> data);
typedef SendBinary = Future<void> Function(Uint8List data);

class FileTransferPage extends StatefulWidget {
  final SendJson onSendJson;
  final SendBinary onSendBinary;
  final String peerHost;

  const FileTransferPage({
    super.key,
    required this.onSendJson,
    required this.onSendBinary,
    required this.peerHost,
  });

  @override
  State<FileTransferPage> createState() => FileTransferPageState();
}

class FileTransferPageState extends State<FileTransferPage> {
  final Map<String, dynamic> _receivingFiles = {};
  String? _currentReceiveId;
  String? _receiveDirectoryPath;
  int? _androidSdkVersion;

  String? _sendingFileId;
  Completer<void>? _acceptCompleter;
  Completer<void>? _ackCompleter;
  Completer<void>? _completeCompleter;
  int? _expectedSendSize;
  String? _expectedSendHash;
  String _sendTransport = 'websocket';
  int? _sendHttpPort;

  HttpServer? _fastUploadServer;

  final _sendProgress = _TransferProgress();
  final _receiveProgress = _TransferProgress();
  List<_TransferRecord> _transferRecords = [];

  @override
  void initState() {
    super.initState();
    _fastTransferChannel.setMethodCallHandler(_handleFastTransferMethod);
    _loadLogs();
    unawaited(_startFastUploadServer());
  }

  @override
  void dispose() {
    _abortTransfers('页面已关闭', updateUi: false);
    unawaited(_fastUploadServer?.close(force: true));
    _fastTransferChannel.setMethodCallHandler(null);
    super.dispose();
  }

  Future<void> _loadLogs() async {
    final prefs = await SharedPreferences.getInstance();
    final encodedRecords = prefs.getStringList(_transferRecordsKey);
    final records = <_TransferRecord>[];
    if (encodedRecords != null) {
      for (final encoded in encodedRecords) {
        try {
          records.add(_TransferRecord.fromJson(jsonDecode(encoded)));
        } catch (_) {
          // Ignore a single damaged record instead of losing the whole list.
        }
      }
    } else {
      final legacyLogs = prefs.getStringList('transfer_logs') ?? const [];
      for (var i = 0; i < legacyLogs.length; i++) {
        records.add(_TransferRecord.legacy(legacyLogs[i], i));
      }
    }
    if (!mounted) return;
    setState(() {
      _transferRecords = records;
    });
  }

  Future<void> _saveLogs() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setStringList(
      _transferRecordsKey,
      _transferRecords.map((record) => jsonEncode(record.toJson())).toList(),
    );
  }

  void _clearLogs() {
    setState(() => _transferRecords.clear());
    unawaited(_saveLogs());
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        Container(
          padding: const EdgeInsets.all(16),
          color: Colors.blue.shade50,
          child: Row(
            mainAxisAlignment: MainAxisAlignment.spaceEvenly,
            children: [
              ElevatedButton.icon(
                onPressed: _sendingFileId == null ? _pickAndSendFiles : null,
                icon: const Icon(Icons.upload_file),
                label: const Text('发送文件'),
              ),
              ElevatedButton.icon(
                onPressed: _openReceiveFolder,
                icon: const Icon(Icons.folder_open),
                label: const Text('接收目录'),
              ),
            ],
          ),
        ),
        const Divider(height: 1),
        Padding(
          padding: const EdgeInsets.fromLTRB(12, 8, 12, 4),
          child: Column(
            children: [
              _buildProgressCard('发送', Icons.upload, _sendProgress),
              const SizedBox(height: 6),
              _buildProgressCard('接收', Icons.download, _receiveProgress),
            ],
          ),
        ),
        const Divider(height: 1),
        Expanded(
          child: _transferRecords.isEmpty
              ? const Center(child: Text('暂无传输记录'))
              : ListView.builder(
                  itemCount: _transferRecords.length,
                  itemBuilder: (ctx, i) {
                    final record =
                        _transferRecords[_transferRecords.length - 1 - i];
                    return _buildLogItem(record);
                  },
                ),
        ),
        if (_transferRecords.isNotEmpty)
          TextButton(
            onPressed: _clearLogs,
            child: const Text('清空记录', style: TextStyle(color: Colors.grey)),
          ),
      ],
    );
  }

  Future<void> _pickAndSendFiles() async {
    final result = await FilePicker.platform.pickFiles(allowMultiple: true);
    if (result == null) return;

    for (final picked in result.files) {
      if (picked.path == null) continue;
      final sourceUri = picked.identifier;
      if (sourceUri != null && sourceUri.isNotEmpty) {
        unawaited(
          _platformChannel.invokeMethod<bool>('persistUriPermission', {
            'uri': sourceUri,
          }),
        );
      }
      await _sendFile(
        File(picked.path!),
        picked.name,
        picked.size,
        sourceUri: sourceUri,
      );
    }
  }

  Future<void> sendSharedFiles(List<dynamic> sharedFiles) async {
    if (_sendingFileId != null) {
      throw StateError('已有文件正在发送，请稍后重新分享');
    }

    for (final rawFile in sharedFiles) {
      if (rawFile is! Map) continue;
      final uri = rawFile['uri']?.toString();
      final name = rawFile['name']?.toString();
      if (uri == null || uri.isEmpty) continue;

      File? cachedFile;
      try {
        final prepared = await _platformChannel
            .invokeMethod<Map<Object?, Object?>>('cacheSharedUri', {
              'uri': uri,
              'name': name,
            });
        final path = prepared?['path']?.toString();
        final preparedName = prepared?['name']?.toString();
        final rawSize = prepared?['size'];
        if (path == null ||
            path.isEmpty ||
            preparedName == null ||
            rawSize is! num) {
          throw StateError('无法读取分享文件');
        }

        cachedFile = File(path);
        await _sendFile(
          cachedFile,
          preparedName,
          rawSize.toInt(),
          sourceUri: uri,
        );
      } catch (error) {
        _log('❌ 分享发送失败: ${name ?? uri}, $error');
        rethrow;
      } finally {
        try {
          if (cachedFile != null && await cachedFile.exists()) {
            await cachedFile.delete();
          }
        } catch (_) {
          // Stale shared cache files are cleaned by the native bridge later.
        }
      }
    }
  }

  Future<void> _sendFile(
    File file,
    String name,
    int size, {
    String? sourceUri,
  }) async {
    if (size < 0 || size > _maxFileSize) {
      _log('❌ 文件大小超出限制: $name');
      return;
    }

    final fileId =
        '${DateTime.now().microsecondsSinceEpoch}-${name.hashCode.abs()}';
    final httpToken = _createTransferToken();
    RandomAccessFile? raf;
    _sendingFileId = fileId;
    _sendTransport = 'websocket';
    _sendHttpPort = null;
    _acceptCompleter = _newCompleter();
    _completeCompleter = _newCompleter();
    _beginProgress(_sendProgress, name: name, total: size, status: '等待接收端');

    try {
      await widget.onSendJson({
        'type': 'FILE_OFFER',
        'file_id': fileId,
        'name': name,
        'size': size,
        'http_token': httpToken,
      });
      await _acceptCompleter!.future.timeout(const Duration(seconds: 15));
      _beginProgress(
        _sendProgress,
        name: name,
        total: size,
        status: '发送中',
        transport: _sendTransport,
      );

      var sent = 0;
      String digestValue;
      if (_sendTransport == 'http') {
        final port = _sendHttpPort;
        if (port == null || widget.peerHost.isEmpty) {
          throw StateError('HTTP 高速通道地址无效');
        }
        final result = await _fastTransferChannel
            .invokeMethod<Map<Object?, Object?>>('uploadFile', {
              'path': file.path,
              'host': widget.peerHost,
              'port': port,
              'file_id': fileId,
              'token': httpToken,
              'size': size,
            });
        final rawTransferred = result?['transferred'];
        final rawHash = result?['sha256'];
        if (rawTransferred is! num ||
            rawHash is! String ||
            rawHash.length != 64) {
          throw StateError('原生高速传输结果无效');
        }
        sent = rawTransferred.toInt();
        digestValue = rawHash;
      } else {
        raf = await file.open(mode: FileMode.read);
        final digestCollector = _DigestCollector();
        final hashSink = sha256.startChunkedConversion(digestCollector);
        var bytesInWindow = 0;

        while (true) {
          final chunk = await raf.read(_chunkSize);
          if (chunk.isEmpty) break;

          hashSink.add(chunk);
          final waitsForWindow = bytesInWindow + chunk.length >= _ackWindow;
          if (waitsForWindow) _ackCompleter = _newCompleter();
          await widget.onSendBinary(chunk);
          sent += chunk.length;
          _updateProgress(_sendProgress, transferred: sent);

          if (waitsForWindow) {
            await _ackCompleter!.future.timeout(const Duration(seconds: 30));
            bytesInWindow = 0;
          } else {
            bytesInWindow += chunk.length;
          }
        }
        hashSink.close();
        final digest = digestCollector.value;
        if (digest == null) throw StateError('无法计算文件哈希');
        digestValue = digest.toString();
      }

      _expectedSendSize = size;
      _expectedSendHash = digestValue;
      _updateProgress(
        _sendProgress,
        transferred: sent,
        status: '校验中',
        force: true,
      );

      await widget.onSendJson({
        'type': 'FILE_END',
        'file_id': fileId,
        'size': size,
        'sha256': _expectedSendHash,
      });
      await _completeCompleter!.future.timeout(const Duration(seconds: 60));
      _updateProgress(
        _sendProgress,
        transferred: size,
        status: '已完成',
        force: true,
      );
      _log(
        '✅ 发送并校验成功: $name',
        direction: 'send',
        fileName: name,
        filePath: file.path,
        sourceUri: sourceUri,
      );
    } catch (e) {
      _updateProgress(
        _sendProgress,
        status: '失败',
        message: e.toString(),
        force: true,
      );
      _log(
        '❌ 发送失败: $name, $e',
        direction: 'send',
        fileName: name,
        filePath: file.path,
        sourceUri: sourceUri,
      );
    } finally {
      await raf?.close();
      _sendingFileId = null;
      _acceptCompleter = null;
      _ackCompleter = null;
      _completeCompleter = null;
      _expectedSendSize = null;
      _expectedSendHash = null;
      _sendTransport = 'websocket';
      _sendHttpPort = null;
      if (mounted) setState(() {});
    }
  }

  Future<void> handleFileMessage(Map<String, dynamic> data) async {
    final type = data['type'];
    final fileId = data['file_id'];
    if (fileId is! String) return;

    if (type == 'FILE_OFFER') {
      await _startReceive(
        fileId,
        data['name'],
        data['size'],
        data['http_token'],
      );
    } else if (type == 'FILE_END') {
      await _finishReceive(fileId, data['size'], data['sha256']);
    } else if (fileId == _sendingFileId && type == 'FILE_ACCEPT') {
      final port = data['port'];
      if (data['transport'] == 'http' &&
          port is int &&
          port > 0 &&
          port <= 65535 &&
          widget.peerHost.isNotEmpty) {
        _sendTransport = 'http';
        _sendHttpPort = port;
      }
      _completeSafely(_acceptCompleter);
    } else if (fileId == _sendingFileId && type == 'FILE_ACK') {
      _completeSafely(_ackCompleter);
    } else if (fileId == _sendingFileId && type == 'FILE_COMPLETE') {
      if (data['size'] == _expectedSendSize &&
          data['sha256'] == _expectedSendHash) {
        _completeSafely(_completeCompleter);
      } else {
        _completeErrorSafely(_completeCompleter, '接收端完成确认校验失败');
      }
    } else if (type == 'FILE_ERROR') {
      final message = data['message']?.toString() ?? '对端拒绝了文件';
      if (fileId == _sendingFileId) {
        _completeErrorSafely(_acceptCompleter, message);
        _completeErrorSafely(_ackCompleter, message);
        _completeErrorSafely(_completeCompleter, message);
      }
      if (fileId == _currentReceiveId) {
        await _failReceive(fileId, message, notifyPeer: false);
      }
    }
  }

  Future<void> handleBinaryMessage(List<int> bytes) async {
    final fileId = _currentReceiveId;
    if (fileId == null) return;
    if (_receivingFiles[fileId]?['transport'] == 'http') return;
    await _writeChunk(fileId, bytes);
  }

  void handleDisconnect() {
    _abortTransfers('连接已断开');
  }

  Future<void> _startReceive(
    String fileId,
    dynamic rawName,
    dynamic rawSize,
    dynamic rawHttpToken,
  ) async {
    if (_currentReceiveId != null) {
      await _sendError(fileId, '接收端正忙');
      return;
    }
    if (rawName is! String ||
        rawName.isEmpty ||
        rawName.length > 255 ||
        rawName.contains('\u0000')) {
      await _sendError(fileId, '无效的文件名');
      return;
    }
    if (rawSize is! int || rawSize < 0 || rawSize > _maxFileSize) {
      await _sendError(fileId, '文件大小超出限制');
      return;
    }

    final safeName = rawName.replaceAll('\\', '/').split('/').last;
    if (safeName.isEmpty || safeName == '.' || safeName == '..') {
      await _sendError(fileId, '无效的文件名');
      return;
    }

    try {
      final saveDir = await _getReceiveDirectory();
      final paths = await _uniquePaths(saveDir, safeName);
      final sink = File(paths.temp).openWrite();
      final digestCollector = _DigestCollector();
      final hashSink = sha256.startChunkedConversion(digestCollector);
      final useHttp =
          _fastUploadServer != null &&
          rawHttpToken is String &&
          rawHttpToken.length >= 32 &&
          rawHttpToken.length <= 256;

      _receivingFiles[fileId] = {
        'sink': sink,
        'path': paths.finalPath,
        'temp_path': paths.temp,
        'name': safeName,
        'size': rawSize,
        'received': 0,
        'bytes_since_ack': 0,
        'hash_sink': hashSink,
        'digest_collector': digestCollector,
        'transport': useHttp ? 'http' : 'websocket',
        'http_token': useHttp ? rawHttpToken : null,
      };
      _currentReceiveId = fileId;
      _beginProgress(
        _receiveProgress,
        name: safeName,
        total: rawSize,
        status: '接收中',
        transport: useHttp ? 'http' : 'websocket',
      );
      final accept = <String, dynamic>{
        'type': 'FILE_ACCEPT',
        'file_id': fileId,
      };
      if (useHttp) {
        accept
          ..['transport'] = 'http'
          ..['port'] = _fastUploadServer!.port;
      }
      await widget.onSendJson(accept);
    } catch (e) {
      _log('❌ 创建文件失败: $e');
      if (_currentReceiveId == fileId) {
        await _failReceive(fileId, '无法创建接收文件', notifyPeer: false);
      }
      await _sendError(fileId, '无法创建接收文件');
    }
  }

  Future<void> _writeChunk(String fileId, List<int> bytes) async {
    final info = _receivingFiles[fileId];
    if (info == null) return;
    if (info['received'] + bytes.length > info['size']) {
      await _failReceive(fileId, '接收数据超过声明大小');
      return;
    }

    try {
      final IOSink sink = info['sink'];
      final ByteConversionSink hashSink = info['hash_sink'];
      sink.add(bytes);
      hashSink.add(bytes);
      info['received'] += bytes.length;
      info['bytes_since_ack'] += bytes.length;
      _updateProgress(_receiveProgress, transferred: info['received']);

      if (info['bytes_since_ack'] >= _ackWindow) {
        // Acknowledge only after the OS has accepted the buffered data.
        await sink.flush();
        info['bytes_since_ack'] = 0;
        if (info['transport'] != 'http') {
          await widget.onSendJson({
            'type': 'FILE_ACK',
            'file_id': fileId,
            'received': info['received'],
          });
        }
      }
    } catch (e) {
      await _failReceive(fileId, '写入文件失败: $e');
    }
  }

  Future<void> _finishReceive(
    String fileId,
    dynamic declaredSize,
    dynamic expectedHash,
  ) async {
    final info = _receivingFiles[fileId];
    if (info == null) {
      await _sendError(fileId, '没有对应的接收任务');
      return;
    }
    if (declaredSize != info['size'] || info['received'] != info['size']) {
      await _failReceive(fileId, '文件大小校验失败');
      return;
    }

    _updateProgress(
      _receiveProgress,
      transferred: info['received'],
      status: '校验中',
      force: true,
    );
    try {
      final IOSink sink = info['sink'];
      final ByteConversionSink hashSink = info['hash_sink'];
      final _DigestCollector collector = info['digest_collector'];
      await sink.flush();
      await sink.close();
      hashSink.close();

      final actualHash = collector.value?.toString();
      if (expectedHash is! String ||
          actualHash == null ||
          expectedHash.toLowerCase() != actualHash) {
        await _deletePartial(info['temp_path']);
        _receivingFiles.remove(fileId);
        _currentReceiveId = null;
        await _sendError(fileId, '文件哈希校验失败');
        _updateProgress(
          _receiveProgress,
          status: '失败',
          message: '文件哈希校验失败',
          force: true,
        );
        _log('❌ 接收失败: ${info['name']}，哈希不匹配');
        return;
      }

      await File(info['temp_path']).rename(info['path']);
      _receivingFiles.remove(fileId);
      _currentReceiveId = null;
      try {
        await widget.onSendJson({
          'type': 'FILE_COMPLETE',
          'file_id': fileId,
          'size': info['received'],
          'sha256': actualHash,
        });
      } catch (_) {
        // The file is already verified and atomically saved. A disconnect at
        // this point must not delete or misreport the completed file.
      }
      _updateProgress(
        _receiveProgress,
        transferred: info['received'],
        status: '已完成',
        force: true,
      );
      _log(
        '✅ 接收并校验成功: ${info['name']}',
        direction: 'receive',
        fileName: info['name'],
        filePath: info['path'],
      );
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('已保存: ${info['name']}'),
            action: SnackBarAction(
              label: '打开',
              onPressed: () => _safeOpenFile(info['path']),
            ),
          ),
        );
      }
    } catch (e) {
      if (_receivingFiles.containsKey(fileId)) {
        await _failReceive(fileId, '完成文件失败: $e');
      } else {
        _log('⚠️ 文件已保存，但完成确认失败: $e');
      }
    }
  }

  Future<void> _failReceive(
    String fileId,
    String message, {
    bool notifyPeer = true,
  }) async {
    final info = _receivingFiles.remove(fileId);
    if (_currentReceiveId == fileId) _currentReceiveId = null;
    if (info != null) {
      try {
        final IOSink sink = info['sink'];
        await sink.close();
      } catch (_) {}
      await _deletePartial(info['temp_path']);
    }
    if (notifyPeer) await _sendError(fileId, message);
    _updateProgress(
      _receiveProgress,
      status: '失败',
      message: message,
      force: true,
    );
    _log('❌ 接收失败: $message');
  }

  Future<void> _sendError(String fileId, String message) async {
    try {
      await widget.onSendJson({
        'type': 'FILE_ERROR',
        'file_id': fileId,
        'message': message,
      });
    } catch (_) {}
  }

  void _abortTransfers(String reason, {bool updateUi = true}) {
    _completeErrorSafely(_acceptCompleter, reason);
    _completeErrorSafely(_ackCompleter, reason);
    _completeErrorSafely(_completeCompleter, reason);

    if (_sendingFileId != null) {
      if (_sendTransport == 'http') {
        unawaited(_fastTransferChannel.invokeMethod<void>('cancelUpload'));
      }
      _sendProgress
        ..status = '已取消'
        ..message = reason;
    }

    final active = _currentReceiveId;
    if (active != null) {
      final info = _receivingFiles.remove(active);
      _currentReceiveId = null;
      if (info != null) {
        try {
          final IOSink sink = info['sink'];
          sink.close();
        } catch (_) {}
        unawaited(_deletePartial(info['temp_path']));
      }
      _receiveProgress
        ..status = '已取消'
        ..message = reason;
    }
    if (updateUi && mounted) setState(() {});
  }

  Future<dynamic> _handleFastTransferMethod(MethodCall call) async {
    if (call.method != 'progress' || call.arguments is! Map) return null;
    final data = call.arguments as Map;
    if (data['file_id'] != _sendingFileId) return null;
    final transferred = data['transferred'];
    if (transferred is num) {
      _updateProgress(_sendProgress, transferred: transferred.toInt());
    }
    return null;
  }

  void _beginProgress(
    _TransferProgress progress, {
    required String name,
    required int total,
    required String status,
    String transport = 'websocket',
  }) {
    final now = DateTime.now();
    progress
      ..name = name
      ..total = total
      ..transferred = 0
      ..status = status
      ..transport = transport
      ..message = null
      ..speedBytesPerSecond = 0
      ..lastSampleBytes = 0
      ..lastSampleAt = now
      ..lastUiAt = now;
    if (mounted) setState(() {});
  }

  void _updateProgress(
    _TransferProgress progress, {
    int? transferred,
    String? status,
    String? message,
    bool force = false,
  }) {
    final now = DateTime.now();
    if (transferred != null) progress.transferred = transferred;
    if (status != null) progress.status = status;
    progress.message = message;

    final lastUiAt = progress.lastUiAt;
    if (!force &&
        lastUiAt != null &&
        now.difference(lastUiAt) < const Duration(milliseconds: 250)) {
      return;
    }

    final lastSampleAt = progress.lastSampleAt;
    final elapsedMicros = lastSampleAt == null
        ? 0
        : now.difference(lastSampleAt).inMicroseconds;
    final byteDelta = progress.transferred - progress.lastSampleBytes;
    if (elapsedMicros > 0 && byteDelta > 0) {
      final instant =
          byteDelta * Duration.microsecondsPerSecond / elapsedMicros;
      progress.speedBytesPerSecond = progress.speedBytesPerSecond <= 0
          ? instant
          : progress.speedBytesPerSecond * 0.7 + instant * 0.3;
    }
    progress
      ..lastSampleBytes = progress.transferred
      ..lastSampleAt = now
      ..lastUiAt = now;
    if (mounted) setState(() {});
  }

  Widget _buildProgressCard(
    String title,
    IconData icon,
    _TransferProgress progress,
  ) {
    final total = progress.total;
    final transferred = progress.transferred
        .clamp(0, total > 0 ? total : progress.transferred)
        .toInt();
    final double ratio = total > 0
        ? (transferred / total).clamp(0.0, 1.0).toDouble()
        : progress.status == '已完成'
        ? 1.0
        : 0.0;
    final speed = progress.speedBytesPerSecond > 0
        ? '${_formatSize(progress.speedBytesPerSecond.round())}/s'
        : '--';
    final isFailure = progress.status == '失败' || progress.status == '已取消';
    final color = isFailure
        ? Colors.red
        : progress.status == '已完成'
        ? Colors.green
        : Colors.blue;

    return DecoratedBox(
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.06),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: color.withValues(alpha: 0.25)),
      ),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(icon, size: 18, color: color),
                const SizedBox(width: 6),
                Expanded(
                  child: Text(
                    '$title：${progress.status}${progress.name.isEmpty ? '' : ' · ${progress.name}'}',
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 6),
            LinearProgressIndicator(
              value: ratio,
              minHeight: 6,
              color: color,
              backgroundColor: color.withValues(alpha: 0.12),
            ),
            const SizedBox(height: 4),
            Text(
              '${_formatSize(transferred)} / ${_formatSize(total)} · '
              '${(ratio * 100).toStringAsFixed(1)}% · $speed'
              '${progress.transport == 'http' ? ' · HTTP 高速' : ''}'
              '${progress.message == null ? '' : ' · ${progress.message}'}',
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: Theme.of(
                context,
              ).textTheme.bodySmall?.copyWith(color: Colors.grey.shade700),
            ),
          ],
        ),
      ),
    );
  }

  Future<void> _startFastUploadServer() async {
    try {
      try {
        _fastUploadServer = await HttpServer.bind(
          InternetAddress.anyIPv4,
          _fastTransferPort,
          shared: false,
        );
      } on SocketException {
        _fastUploadServer = await HttpServer.bind(
          InternetAddress.anyIPv4,
          0,
          shared: false,
        );
      }
      _fastUploadServer!.listen(
        (request) => unawaited(_handleFastUpload(request)),
        onError: (Object error, StackTrace stackTrace) {
          _log('⚠️ HTTP 高速接收通道异常: $error');
        },
      );
    } catch (e) {
      _fastUploadServer = null;
      _log('⚠️ HTTP 高速通道不可用，将使用 WebSocket: $e');
    }
  }

  Future<void> _handleFastUpload(HttpRequest request) async {
    if (request.method != 'POST' || request.uri.path != _fastTransferPath) {
      request.response.statusCode = HttpStatus.notFound;
      await request.response.close();
      return;
    }

    final fileId = request.headers.value('X-Phone2PC-File-ID');
    final token = request.headers.value('X-Phone2PC-Token');
    final info = fileId == null ? null : _receivingFiles[fileId];
    final valid =
        info != null &&
        fileId == _currentReceiveId &&
        info['transport'] == 'http' &&
        token != null &&
        _secureEquals(token, info['http_token']) &&
        request.contentLength == info['size'];
    if (!valid || fileId == null) {
      request.response.statusCode = HttpStatus.forbidden;
      await request.response.close();
      return;
    }
    final activeFileId = fileId;

    try {
      await for (final chunk in request) {
        if (!_receivingFiles.containsKey(activeFileId)) {
          throw StateError('接收任务已结束');
        }
        await _writeChunk(activeFileId, chunk);
      }
      final current = _receivingFiles[activeFileId];
      if (current == null || current['received'] != current['size']) {
        if (current != null) {
          await _failReceive(activeFileId, 'HTTP 文件流提前结束');
        }
        request.response.statusCode = HttpStatus.conflict;
      } else {
        request.response.statusCode = HttpStatus.ok;
      }
    } catch (e) {
      if (_receivingFiles.containsKey(activeFileId)) {
        await _failReceive(activeFileId, 'HTTP 接收失败: $e');
      }
      request.response.statusCode = HttpStatus.internalServerError;
    }
    await request.response.close();
  }

  String _createTransferToken() {
    final random = Random.secure();
    return base64UrlEncode(List<int>.generate(32, (_) => random.nextInt(256)));
  }

  bool _secureEquals(String left, dynamic right) {
    if (right is! String || left.length != right.length) return false;
    var difference = 0;
    for (var i = 0; i < left.length; i++) {
      difference |= left.codeUnitAt(i) ^ right.codeUnitAt(i);
    }
    return difference == 0;
  }

  Future<Directory> _getReceiveDirectory({
    bool requestPermission = false,
  }) async {
    if (_receiveDirectoryPath != null) return Directory(_receiveDirectoryPath!);
    if (!await _ensureStoragePermission(requestPermission: requestPermission)) {
      throw const FileSystemException('请先点击“接收目录”并授予文件访问权限');
    }
    final downloads = await _platformChannel.invokeMethod<String>(
      'getPublicDownloadDirectory',
    );
    if (downloads == null || downloads.isEmpty) {
      throw const FileSystemException('无法获取系统下载目录');
    }
    final directory = Directory('$downloads${Platform.pathSeparator}Phone2PC');
    await directory.create(recursive: true);
    _receiveDirectoryPath = directory.path;
    return directory;
  }

  Future<bool> _ensureStoragePermission({
    required bool requestPermission,
  }) async {
    if (!Platform.isAndroid) return true;
    _androidSdkVersion ??=
        await _platformChannel.invokeMethod<int>('getSdkInt') ?? 30;
    final permission = _androidSdkVersion! >= 30
        ? Permission.manageExternalStorage
        : Permission.storage;
    if (await permission.isGranted) return true;
    if (!requestPermission) return false;
    final status = await permission.request();
    return status.isGranted;
  }

  Future<_ReceivePaths> _uniquePaths(Directory directory, String name) async {
    final dot = name.lastIndexOf('.');
    final base = dot <= 0 ? name : name.substring(0, dot);
    final ext = dot <= 0 ? '' : name.substring(dot);
    var counter = 0;
    while (true) {
      final suffix = counter == 0 ? '' : '_$counter';
      final finalPath =
          '${directory.path}${Platform.pathSeparator}$base$suffix$ext';
      final tempPath = '$finalPath.part';
      if (!await File(finalPath).exists() && !await File(tempPath).exists()) {
        return _ReceivePaths(finalPath, tempPath);
      }
      counter++;
    }
  }

  Future<void> _deletePartial(String path) async {
    try {
      final file = File(path);
      if (await file.exists()) await file.delete();
    } catch (_) {}
  }

  void _log(
    String msg, {
    String? direction,
    String? fileName,
    String? filePath,
    String? sourceUri,
  }) {
    if (!mounted) return;
    setState(() {
      _transferRecords.add(
        _TransferRecord(
          id: '${DateTime.now().microsecondsSinceEpoch}-${_transferRecords.length}',
          message: msg,
          createdAt: DateTime.now().millisecondsSinceEpoch,
          direction: direction,
          fileName: fileName,
          filePath: filePath,
          sourceUri: sourceUri,
        ),
      );
      if (_transferRecords.length > 200) _transferRecords.removeAt(0);
    });
    unawaited(_saveLogs());
  }

  String _formatSize(int bytes) {
    if (bytes < 1024) return '$bytes B';
    if (bytes < 1024 * 1024) return '${(bytes / 1024).toStringAsFixed(1)} KB';
    if (bytes < 1024 * 1024 * 1024) {
      return '${(bytes / 1024 / 1024).toStringAsFixed(1)} MB';
    }
    return '${(bytes / 1024 / 1024 / 1024).toStringAsFixed(1)} GB';
  }

  Future<bool> _safeOpenFile(String path) async {
    if (!await File(path).exists()) return false;
    final result = await OpenFile.open(path);
    if (result.type != ResultType.done) {
      await Share.shareXFiles([XFile(path)]);
    }
    return true;
  }

  Future<void> _openReceiveFolder() async {
    try {
      final directory = await _getReceiveDirectory(requestPermission: true);
      final opened = await _platformChannel.invokeMethod<bool>(
        'openDirectory',
        {'path': directory.path},
      );
      if (opened != true && mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('无法启动文件管理器，请手动打开：${directory.path}')),
        );
      }
    } catch (e, stackTrace) {
      debugPrint('无法打开接收目录: $e\n$stackTrace');
      if (mounted) {
        final path =
            _receiveDirectoryPath ?? '/storage/emulated/0/Download/Phone2PC';
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text('无法启动系统文件管理器，请手动打开：$path')));
      }
    }
  }

  Widget _buildLogItem(_TransferRecord record) {
    final msg = record.message;
    return Dismissible(
      key: ValueKey(record.id),
      direction: DismissDirection.startToEnd,
      background: Container(
        color: Colors.red.shade400,
        padding: const EdgeInsets.symmetric(horizontal: 20),
        alignment: Alignment.centerLeft,
        child: const Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.delete_outline, color: Colors.white),
            SizedBox(width: 8),
            Text('删除记录', style: TextStyle(color: Colors.white)),
          ],
        ),
      ),
      onDismissed: (_) => _deleteTransferRecord(record.id),
      child: ListTile(
        dense: true,
        leading: Icon(
          msg.contains('✅')
              ? Icons.check_circle
              : msg.contains('❌')
              ? Icons.error
              : record.direction == 'send'
              ? Icons.upload
              : Icons.download,
          size: 20,
          color: msg.contains('✅')
              ? Colors.green
              : msg.contains('❌')
              ? Colors.red
              : Colors.grey,
        ),
        title: Text(msg, maxLines: 2, overflow: TextOverflow.ellipsis),
        trailing: record.hasFileReference
            ? const Icon(Icons.chevron_right, size: 20)
            : null,
        onTap: record.hasFileReference
            ? () => unawaited(_openTransferRecord(record))
            : null,
        onLongPress: () => _showTransferRecordMenu(record),
      ),
    );
  }

  void _deleteTransferRecord(String id) {
    if (!mounted) return;
    setState(() => _transferRecords.removeWhere((record) => record.id == id));
    unawaited(_saveLogs());
  }

  Future<void> _openTransferRecord(_TransferRecord record) async {
    var opened = false;
    final sourceUri = record.sourceUri;
    if (sourceUri != null && sourceUri.isNotEmpty) {
      opened =
          await _platformChannel.invokeMethod<bool>('openFileUri', {
            'uri': sourceUri,
            'name': record.fileName,
          }) ??
          false;
    }
    final path = record.filePath;
    if (!opened && path != null && path.isNotEmpty) {
      opened = await _safeOpenFile(path);
    }
    if (!opened && mounted) {
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(const SnackBar(content: Text('原文件已移动、删除或访问权限已失效')));
    }
  }

  Future<void> _openTransferRecordDirectory(_TransferRecord record) async {
    final opened =
        await _platformChannel.invokeMethod<bool>('openParentDirectory', {
          'path': record.filePath,
          'uri': record.sourceUri,
        }) ??
        false;
    if (!opened && mounted) {
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(const SnackBar(content: Text('无法定位原文件目录，文件可能来自应用私有空间')));
    }
  }

  Future<void> _showTransferRecordMenu(_TransferRecord record) async {
    await showModalBottomSheet<void>(
      context: context,
      showDragHandle: true,
      builder: (sheetContext) => SafeArea(
        child: Wrap(
          children: [
            ListTile(
              leading: const Icon(Icons.open_in_new),
              title: const Text('打开'),
              enabled: record.hasFileReference,
              onTap: record.hasFileReference
                  ? () {
                      Navigator.pop(sheetContext);
                      unawaited(_openTransferRecord(record));
                    }
                  : null,
            ),
            ListTile(
              leading: const Icon(Icons.folder_open),
              title: const Text('打开目录'),
              enabled: record.hasFileReference,
              onTap: record.hasFileReference
                  ? () {
                      Navigator.pop(sheetContext);
                      unawaited(_openTransferRecordDirectory(record));
                    }
                  : null,
            ),
            ListTile(
              leading: const Icon(Icons.delete_outline, color: Colors.red),
              title: const Text('删除传输记录', style: TextStyle(color: Colors.red)),
              onTap: () {
                Navigator.pop(sheetContext);
                _deleteTransferRecord(record.id);
              },
            ),
          ],
        ),
      ),
    );
  }

  void _completeSafely(Completer<void>? completer) {
    if (completer != null && !completer.isCompleted) completer.complete();
  }

  void _completeErrorSafely(Completer<void>? completer, String message) {
    if (completer != null && !completer.isCompleted) {
      completer.completeError(StateError(message));
    }
  }

  Completer<void> _newCompleter() {
    final completer = Completer<void>();
    // A disconnect may fail a later protocol stage before it is awaited.
    // Attach a listener immediately to avoid an unhandled zone error.
    completer.future.catchError((Object _) {});
    return completer;
  }
}

class _DigestCollector implements Sink<Digest> {
  Digest? value;

  @override
  void add(Digest data) => value = data;

  @override
  void close() {}
}

class _ReceivePaths {
  final String finalPath;
  final String temp;

  const _ReceivePaths(this.finalPath, this.temp);
}

class _TransferRecord {
  final String id;
  final String message;
  final int createdAt;
  final String? direction;
  final String? fileName;
  final String? filePath;
  final String? sourceUri;

  const _TransferRecord({
    required this.id,
    required this.message,
    required this.createdAt,
    this.direction,
    this.fileName,
    this.filePath,
    this.sourceUri,
  });

  bool get hasFileReference =>
      (filePath != null && filePath!.isNotEmpty) ||
      (sourceUri != null && sourceUri!.isNotEmpty);

  factory _TransferRecord.fromJson(dynamic value) {
    if (value is! Map) throw const FormatException('记录格式无效');
    final id = value['id'];
    final message = value['message'];
    final createdAt = value['created_at'];
    if (id is! String || message is! String || createdAt is! num) {
      throw const FormatException('记录字段无效');
    }
    return _TransferRecord(
      id: id,
      message: message,
      createdAt: createdAt.toInt(),
      direction: value['direction'] as String?,
      fileName: value['file_name'] as String?,
      filePath: value['file_path'] as String?,
      sourceUri: value['source_uri'] as String?,
    );
  }

  factory _TransferRecord.legacy(String message, int index) {
    return _TransferRecord(
      id: 'legacy-$index-${message.hashCode}',
      message: message,
      createdAt: index,
    );
  }

  Map<String, dynamic> toJson() => {
    'id': id,
    'message': message,
    'created_at': createdAt,
    'direction': direction,
    'file_name': fileName,
    'file_path': filePath,
    'source_uri': sourceUri,
  };
}

class _TransferProgress {
  String name = '';
  String status = '空闲';
  String? message;
  String transport = 'websocket';
  int transferred = 0;
  int total = 0;
  double speedBytesPerSecond = 0;
  int lastSampleBytes = 0;
  DateTime? lastSampleAt;
  DateTime? lastUiAt;
}
