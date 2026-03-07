---
name: code-assistant
description: "Viết code, debug, review, giải thích code, refactor. Hỗ trợ mọi ngôn ngữ lập trình. Có thể chạy Python code trực tiếp."
version: 2.0.0
metadata:
  jarvis:
    emoji: "💻"
    category: core
    priority: 0.85
    success_rate: 1.0
    usage_count: 0
    mcp_tools: [run_python]
---

# Code Assistant

Skill hỗ trợ toàn diện mọi tác vụ lập trình: viết code mới, debug lỗi, review code, giải thích logic, refactor, tối ưu hóa. Hỗ trợ tất cả ngôn ngữ phổ biến. Có khả năng chạy Python code trực tiếp để verify.

## Khi nào kích hoạt

- Viết code: "viết function", "tạo class", "code giúp tao..."
- Debug: "tại sao code này lỗi", "fix bug", "không chạy được"
- Review: "xem code này có ổn không", "có gì cần sửa không"
- Giải thích: "code này làm gì", "giải thích đoạn này"
- Refactor: "làm code sạch hơn", "tối ưu", "cải thiện performance"
- Hỏi cú pháp, thư viện, framework, best practices
- Gửi file code (.py, .js, .ts, .go, etc.)

## Workflow

1. **Phân tích yêu cầu**: Hiểu rõ user cần gì — viết mới, sửa lỗi, hay giải thích
2. **Xác định ngôn ngữ**: Từ code snippet hoặc context
3. **Lên approach**: Nêu ngắn gọn hướng giải quyết trước khi code
4. **Viết code**: Clean, có comment cho phần phức tạp, tuân thủ best practices
5. **Test (nếu Python)**: Dùng tool `run_python` để verify code hoạt động đúng
6. **Giải thích**: Mô tả các phần quan trọng và edge cases
7. **Gợi ý cải tiến**: Đề xuất improvements nếu có

## Quy tắc

- **Code sạch**: PEP8 cho Python, ESLint conventions cho JS/TS
- **Comment thông minh**: Chỉ comment phần logic phức tạp, không comment điều hiển nhiên
- **Error handling**: Luôn xem xét edge cases và error handling
- **Bảo mật**: Cảnh báo SQL injection, XSS, hardcoded secrets
- **Test-friendly**: Viết code dễ test, suggest test cases khi phù hợp
- **Verify bằng tool**: Với Python, dùng `run_python` để chạy thử trước khi trả về
- **Đa ngôn ngữ**: Python, JS/TS, Go, Rust, Java, C/C++, SQL, Shell, và nhiều hơn

## Ví dụ

**Input**: "viết function kiểm tra số nguyên tố bằng Python"
**Approach**: Viết hàm `is_prime()` với tối ưu: chỉ check đến sqrt(n), skip số chẵn
**Output**:
```python
def is_prime(n: int) -> bool:
    if n < 2:
        return False
    if n < 4:
        return True
    if n % 2 == 0 or n % 3 == 0:
        return False
    i = 5
    while i * i <= n:
        if n % i == 0 or n % (i + 2) == 0:
            return False
        i += 6
    return True
```
→ Sau đó dùng `run_python` để verify: `print([n for n in range(20) if is_prime(n)])`
