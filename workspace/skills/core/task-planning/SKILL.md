---
name: task-planning
description: "Lập kế hoạch, chia nhỏ task phức tạp, tạo roadmap, quản lý dự án. Giúp user organize công việc hiệu quả."
version: 2.0.0
metadata:
  jarvis:
    emoji: "📋"
    category: core
    priority: 0.7
    success_rate: 1.0
    usage_count: 0
---

# Task Planning

Skill chuyên lập kế hoạch, phân tích task phức tạp thành các bước thực hiện cụ thể, tạo roadmap, và hỗ trợ quản lý dự án.

## Khi nào kích hoạt

- Lập kế hoạch: "lên plan cho...", "plan", "lập kế hoạch"
- Chia task: "chia nhỏ task này", "breakdown", "how to approach"
- Roadmap: "tạo roadmap", "timeline", "lộ trình"
- Ước lượng: "mất bao lâu", "estimate", "đánh giá effort"
- Prioritize: "ưu tiên gì trước", "nên làm gì trước"
- Decision making: "nên chọn A hay B", "so sánh options"

## Workflow

1. **Phân tích mục tiêu**: Hiểu rõ end goal và constraints
2. **Decompose**: Chia thành các milestone hoặc epic
3. **Chi tiết hóa**: Mỗi milestone → subtasks cụ thể, actionable
4. **Dependencies**: Xác định thứ tự, task nào phụ thuộc task nào
5. **Estimate**: Ước lượng effort cho mỗi task (nếu user cần)
6. **Priority**: Đề xuất thứ tự ưu tiên dựa trên impact vs effort
7. **Output format**: Trình bày dạng danh sách có cấu trúc, dễ follow

## Quy tắc

- **Actionable**: Mỗi task phải đủ cụ thể để thực hiện ngay
- **Checkbox format**: Dùng `- [ ]` để user track tiến độ
- **Realistic**: Ước lượng thực tế, thêm buffer 20-30%
- **Flexible**: Đề xuất alternatives, không áp đặt một cách duy nhất
- **Milestone-based**: Chia thành các mốc rõ ràng để đo tiến độ
- **Risk aware**: Nhận diện risks và đề xuất mitigation
- **Follow up**: Hỏi user muốn đi sâu vào phase nào

## Ví dụ

**Input**: "tao muốn học machine learning từ đầu, lên plan giúp"
**Output**:
📋 **Roadmap học Machine Learning**

**Phase 1: Nền tảng (2-3 tuần)**
- [ ] Python basics + NumPy, Pandas
- [ ] Toán: Linear Algebra, Statistics cơ bản
- [ ] Khóa: Andrew Ng's ML course (free, Coursera)

**Phase 2: Core ML (3-4 tuần)**
- [ ] Supervised Learning: Regression, Trees, SVM
- [ ] Unsupervised: K-Means, PCA
- [ ] Kaggle: 3-5 projects thực hành

**Phase 3: Deep Learning (4-6 tuần)**
- [ ] Neural Networks, CNNs, Transformers
- [ ] Framework: PyTorch
- [ ] Capstone project

💡 Bắt đầu Phase 1 ngay, mỗi ngày 1-2h. Muốn tao tìm resources cụ thể?
