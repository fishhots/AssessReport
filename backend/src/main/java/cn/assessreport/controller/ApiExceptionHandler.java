package cn.assessreport.controller;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.*;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MaxUploadSizeExceededException;
import org.springframework.web.server.ResponseStatusException;
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException;
import java.io.IOException;
import java.util.Map;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.http.converter.HttpMessageNotReadableException;

@RestControllerAdvice
public class ApiExceptionHandler {
    private static final Logger log = LoggerFactory.getLogger(ApiExceptionHandler.class);
    @ExceptionHandler(IllegalArgumentException.class)
    ResponseEntity<?> badInput(IllegalArgumentException e) { return error(HttpStatus.BAD_REQUEST, e.getMessage()); }
    @ExceptionHandler(MethodArgumentNotValidException.class)
    ResponseEntity<?> validation(MethodArgumentNotValidException e, HttpServletRequest request) {
        return error(HttpStatus.BAD_REQUEST, request.getRequestURI().startsWith("/api/models/")
            ? "请检查模型名称、密钥、接口地址和额外参数" : "请完整填写学校、年份、模型和资料，并至少选择一个报告章节");
    }
    @ExceptionHandler(HttpMessageNotReadableException.class)
    ResponseEntity<?> invalidBody(HttpMessageNotReadableException e) { return error(HttpStatus.BAD_REQUEST, "提交内容格式不正确，请检查后重试"); }
    @ExceptionHandler(MethodArgumentTypeMismatchException.class)
    ResponseEntity<?> invalidType(MethodArgumentTypeMismatchException e) { return error(HttpStatus.BAD_REQUEST, "参数格式不正确"); }
    @ExceptionHandler(MaxUploadSizeExceededException.class)
    ResponseEntity<?> tooLarge(MaxUploadSizeExceededException e) { return error(HttpStatus.PAYLOAD_TOO_LARGE, "单个文件不能超过 50 MB，单次上传总量不能超过 200 MB"); }
    @ExceptionHandler(ResponseStatusException.class)
    ResponseEntity<?> status(ResponseStatusException e) { return ResponseEntity.status(e.getStatusCode()).body(Map.of("message", e.getReason() == null ? "请求失败" : e.getReason())); }
    @ExceptionHandler(IOException.class)
    ResponseEntity<?> storage(IOException e) { log.error("Workspace storage operation failed", e); return error(HttpStatus.INTERNAL_SERVER_ERROR, "资料保存失败，请检查服务存储后重试"); }
    private ResponseEntity<?> error(HttpStatus status, String message) { return ResponseEntity.status(status).body(Map.of("message", message)); }
}
