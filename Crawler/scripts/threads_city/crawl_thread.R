# ================================================================
# CAO BAI DANG CONG KHAI TREN THREADS THEO TU KHOA
# Ngon ngu: R
# Cach lam: R dieu khien Chrome, doc noi dung dang hien thi, luu CSV
# Khong dung Threads API va khong can THREADS_ACCESS_TOKEN.
# Chi thu thap bai cong khai; hay dung du lieu dung muc dich hoc tap/nghien cuu
# va tuan thu dieu khoan cua Threads.
#
# PHIEN BAN SUA:
# - Chi xuat 1 file CSV duy nhat: comments (khong xuat file posts nua).
# - Sua logic tach "card" bai viet / comment trong trang chi tiet: dung
#   ky thuat dem so nut hanh dong (like/reply/repost) ~ giong ham
#   findContainer o trang tim kiem, thay vi dua vao <time> + dieu kien
#   de trung node, vi day la nguyen nhan khien comments bi rong.
# - comment_id duoc tinh lai theo (nguoi_comment + noi_dung) de dedup
#   dung qua nhieu vong tai (load them) comment, thay vi dua vao thu tu.
# ================================================================

required_packages <- c(
  "chromote", "dplyr", "purrr", "readr", "tibble", "jsonlite", "getPass"
)

missing_packages <- required_packages[
  !vapply(required_packages, requireNamespace, logical(1), quietly = TRUE)
]

if (length(missing_packages) > 0) {
  stop(
    "Thieu goi R: ", paste(missing_packages, collapse = ", "), "\n",
    "Hay chay:\ninstall.packages(c(",
    paste(sprintf('"%s"', missing_packages), collapse = ", "),
    "))",
    call. = FALSE
  )
}

suppressPackageStartupMessages({
  library(chromote)
  library(dplyr)
  library(purrr)
  library(readr)
  library(tibble)
  library(jsonlite)
})

# ----------------------------------------------------------------
# 1. CAU HINH
# ----------------------------------------------------------------

# --- Tu dong xac dinh duong dan tuong doi tu vi tri script ---
# Ho tro ca RStudio (source) va Rscript (command line)
get_script_dir <- function() {
  # 1. Khi chay bang Rscript / commandArgs
  args <- commandArgs(trailingOnly = FALSE)
  file_arg <- grep("^--file=", args, value = TRUE)
  if (length(file_arg) > 0) {
    return(normalizePath(dirname(sub("^--file=", "", file_arg[1]))))
  }
  # 2. Khi source() tu R console
  if (!is.null(sys.frame(1)$ofile)) {
    return(normalizePath(dirname(sys.frame(1)$ofile)))
  }
  # 3. Khi chay trong RStudio bang Source / Ctrl+Shift+S
  if (requireNamespace("rstudioapi", quietly = TRUE) &&
      rstudioapi::isAvailable()) {
    ctx <- rstudioapi::getActiveDocumentContext()
    if (nzchar(ctx$path)) {
      return(normalizePath(dirname(ctx$path)))
    }
  }
  # 4. Fallback: thu muc lam viec hien tai
  return(normalizePath(getwd()))
}

script_dir <- get_script_dir()
# script nam tai: Crawler/scripts/threads_city/
# thread_city.csv nam tai: Crawler/data/thread_city/
# => di len 2 cap roi xuong data/thread_city
output_dir <- normalizePath(file.path(script_dir, "..", "..", "data", "thread_city"), mustWork = FALSE)

cookie_file <- path.expand("~/.threads_chromote_cookies.rds")

login_mode <- "console"
console_login_timeout_seconds <- 60L

max_posts_per_keyword <- 50L
max_scrolls_per_keyword <- 12L

max_comments_per_post <- 10L
max_comment_load_rounds <- 8L

page_load_seconds <- 10
scroll_pause_min <- 2.0
scroll_pause_max <- 4.0
keyword_pause_min <- 3.0
keyword_pause_max <- 6.0
post_pause_min <- 2.0
post_pause_max <- 4.0

search_filter <- "recent"

# Chi cao bai tieng Viet (co dau tieng Viet trong noi dung).
# Dat FALSE de cao tat ca ngon ngu.
only_vietnamese <- TRUE

# Chi cao bai co it nhat 1 comment (bo qua bai 0 reply).
# Dat FALSE de cao ca bai khong co comment.
only_posts_with_comments <- TRUE

keyword_dictionary <- tribble(
  ~nhom,         ~tu_khoa,
  "LGBTQ+",      "LGBTQ+",
  "LGBTQ+",      "LGBT",
  "LGBTQ+",      "đồng tính",
  "LGBTQ+",      "chuyển giới",
  "LGBTQ+",      "song tính",
  "Vùng miền",   "vùng miền",
  "Vùng miền",   "Bắc Kỳ",
  "Vùng miền",   "Nam Kỳ",
  "Tôn giáo",    "tôn giáo",
  "Tôn giáo",    "Phật giáo",
  "Tôn giáo",    "Công giáo",
  "Tôn giáo",    "Thiên Chúa giáo",
  "Tôn giáo",    "Hồi giáo",
  "Tôn giáo",    "đạo Tin Lành",
  "Giới tính",   "giới tính",
  "Giới tính",   "nam giới",
  "Giới tính",   "nữ giới",
  "Giới tính",   "đàn ông",
  "Giới tính",   "phụ nữ",
  "Giới tính",   "bình đẳng giới",
  "Ngoại hình",  "ngoại hình",
  "Ngoại hình",  "béo",
  "Ngoại hình",  "gầy",
  "Ngoại hình",  "xấu",
  "Ngoại hình",  "đẹp",
  "Ngoại hình",  "thấp lùn",
  "Bệnh lý",     "bệnh lý",
  "Bệnh lý",     "khuyết tật",
  "Bệnh lý",     "tự kỷ",
  "Bệnh lý",     "trầm cảm",
  "Bệnh lý",     "HIV"
)

# ----------------------------------------------------------------
# 2. CAC HAM DIEU KHIEN TRINH DUYET
# ----------------------------------------------------------------

empty_search_posts <- function() {
  tibble(
    post_id = character(),
    nguoi_dang = character(),
    link = character(),
    noi_dung_tom_tat = character(),
    so_reply = integer()
  )
}

# Kiem tra chuoi co chua ky tu tieng Viet (dau) khong.
is_vietnamese_text <- function(text) {
  grepl(
    "[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđÀÁẠẢÃÂẦẤẬẨẪĂẰẮẶẲẴÈÉẸẺẼÊỀẾỆỂỄÌÍỊỈĨÒÓỌỎÕÔỒỐỘỔỖƠỜỚỢỞỠÙÚỤỦŨƯỪỨỰỬỮỲÝỴỶỸĐ]",
    text,
    perl = TRUE
  )
}

# Van giu cau truc "post" noi bo (de lay tieu de bai viet ghep vao file
# comment), nhung se KHONG ghi ra CSV rieng nua.
empty_posts_output <- function() {
  tibble(
    post_id = character(),
    tieu_de_bai_viet = character(),
    nguoi_dang = character(),
    noi_dung_bai_viet = character(),
    thoi_gian_dang = character(),
    link_bai_viet = character(),
    so_luot_thich = numeric(),
    so_luot_tra_loi = numeric(),
    thoi_gian_cao = character()
  )
}

empty_comments_output <- function() {
  tibble(
    post_id = character(),
    tieu_de_bai_viet = character(),
    link_bai_viet = character(),
    comment_id = character(),
    nguoi_comment = character(),
    noi_dung_comment = character(),
    thoi_gian_comment = character(),
    so_luot_thich_comment = numeric(),
    so_luot_tra_loi_comment = numeric(),
    dang_tra_loi = character(),
    link_comment = character(),
    thoi_gian_cao = character()
  )
}

clean_comment_content <- function(value) {
  value <- as.character(value)
  value[is.na(value)] <- ""

  value <- gsub(
    "\\b[0-9]+(?:[.,][0-9]+)?\\s*[KMBN]?\\s*(?:views?|lượt xem)\\b",
    " ",
    value,
    ignore.case = TRUE,
    perl = TRUE
  )
  value <- gsub(
    "\\b[0-9]{1,2}/[0-9]{1,2}/[0-9]{2,4}\\b",
    " ",
    value,
    perl = TRUE
  )
  value <- gsub(
    "\\b(?:Reply to|Replying to|Đang trả lời|Trả lời)\\s+@?[A-Za-z0-9._]+\\.{0,3}",
    " ",
    value,
    ignore.case = TRUE,
    perl = TRUE
  )
  value <- gsub(
    "\\bNo replies yet\\b",
    " ",
    value,
    ignore.case = TRUE,
    perl = TRUE
  )
  value <- gsub(
    "\\b(?:Translate|See translation|Dịch|Xem bản dịch)(?:\\s+[0-9]+\\s*/\\s*[0-9]+)?\\b",
    " ",
    value,
    ignore.case = TRUE,
    perl = TRUE
  )
  value <- trimws(gsub("\\s+", " ", value, perl = TRUE))
  value
}

# File comment chi ghi noi dung va thong tin toi thieu de ghep comment voi bai.
comments_for_csv <- function(comments) {
  comments |>
    transmute(
      post_id,
      tieu_de_bai_viet,
      link_bai_viet,
      noi_dung_comment
    )
}

make_post_title <- function(content, max_characters = 160L) {
  content <- gsub("[\r\n]+", " ", as.character(content))
  content <- trimws(gsub("\\s+", " ", content))

  if (!nzchar(content)) {
    return("(Bài viết không có nội dung chữ)")
  }

  first_sentence <- strsplit(content, "(?<=[.!?])\\s+", perl = TRUE)[[1]][1]
  title <- trimws(first_sentence)

  if (nchar(title, type = "chars") > max_characters) {
    title <- paste0(
      substr(title, 1L, max_characters - 1L),
      "…"
    )
  }

  title
}

sleep_random <- function(min_seconds, max_seconds) {
  Sys.sleep(stats::runif(1, min_seconds, max_seconds))
}

navigate_to <- function(browser, url) {
  tryCatch(
    {
      browser$Page$navigate(url, wait_ = TRUE)
    },
    error = function(e) {
      warning("Trang load chậm, tiếp tục cố gắng đọc dữ liệu...")
    }
  )

  Sys.sleep(page_load_seconds)

  invisible(TRUE)
}

evaluate_js <- function(browser, expression) {
  result <- browser$Runtime$evaluate(
    expression = expression,
    returnByValue = TRUE,
    awaitPromise = TRUE
  )

  if (!is.null(result$exceptionDetails)) {
    description <- result$exceptionDetails$exception$description

    if (is.null(description) || !nzchar(description)) {
      description <- result$exceptionDetails$text
    }

    stop("Loi JavaScript trong trang Threads: ", description, call. = FALSE)
  }

  result$result$value
}

page_state_js <- paste(
  c(
    "(() => {",
    "  const bodyText = (document.body?.innerText || '').slice(0, 1200);",
    "  return JSON.stringify({",
    "    url: location.href,",
    "    title: document.title || '',",
    "    body_text: bodyText,",
    "    post_links: document.querySelectorAll('a[href*=\"/post/\"]').length,",
    "    has_password: document.querySelector('input[type=\"password\"]') !== null",
    "  });",
    "})()"
  ),
  collapse = "\n"
)

get_page_state <- function(browser) {
  state_json <- evaluate_js(browser, page_state_js)

  if (is.null(state_json) || !nzchar(state_json)) {
    return(list(
      url = "",
      title = "",
      body_text = "",
      post_links = 0L,
      has_password = FALSE
    ))
  }

  jsonlite::fromJSON(state_json, simplifyVector = TRUE)
}

needs_login <- function(state) {
  isTRUE(state$has_password) ||
    grepl("/login", state$url, fixed = TRUE) ||
    grepl("/accounts/login", state$url, fixed = TRUE)
}

login_form_state_js <- paste(
  c(
    "(() => {",
    "  const visible = (element) => {",
    "    if (!element) return false;",
    "    const style = getComputedStyle(element);",
    "    return style.display !== 'none' && style.visibility !== 'hidden';",
    "  };",
    "  const firstVisible = (selectors) => {",
    "    for (const selector of selectors) {",
    "      const element = Array.from(document.querySelectorAll(selector)).find(visible);",
    "      if (element) return element;",
    "    }",
    "    return null;",
    "  };",
    "  const otp = firstVisible([",
    "    'input[autocomplete=\"one-time-code\"]',",
    "    'input[name=\"verificationCode\"]',",
    "    'input[name=\"security_code\"]',",
    "    'input[inputmode=\"numeric\"]'",
    "  ]);",
    "  const text = (document.body?.innerText || '').slice(0, 3000);",
    "  return JSON.stringify({",
    "    has_username: firstVisible([",
    "      'input[name=\"username\"]',",
    "      'input[autocomplete=\"username\"]',",
    "      'input[type=\"text\"]'",
    "    ]) !== null,",
    "    has_password: firstVisible([",
    "      'input[name=\"password\"]',",
    "      'input[autocomplete=\"current-password\"]',",
    "      'input[type=\"password\"]'",
    "    ]) !== null,",
    "    has_otp: otp !== null,",
    "    has_challenge: /(captcha|xác minh|verify|suspicious|unusual|checkpoint)/i.test(text),",
    "    text: text",
    "  });",
    "})()"
  ),
  collapse = "\n"
)

get_login_form_state <- function(browser) {
  value <- evaluate_js(browser, login_form_state_js)

  if (is.null(value) || !nzchar(value)) {
    return(list(
      has_username = FALSE,
      has_password = FALSE,
      has_otp = FALSE,
      has_challenge = FALSE,
      text = ""
    ))
  }

  jsonlite::fromJSON(value, simplifyVector = TRUE)
}

fill_login_form <- function(browser, username, password) {
  credentials_json <- jsonlite::toJSON(
    list(username = username, password = password),
    auto_unbox = TRUE,
    null = "null"
  )

  expression <- paste0(
    "(() => {\n",
    "  const credentials = ", credentials_json, ";\n",
    "  const visible = (element) => {\n",
    "    if (!element) return false;\n",
    "    const style = getComputedStyle(element);\n",
    "    return style.display !== 'none' && style.visibility !== 'hidden';\n",
    "  };\n",
    "  const firstVisible = (selectors) => {\n",
    "    for (const selector of selectors) {\n",
    "      const element = Array.from(document.querySelectorAll(selector)).find(visible);\n",
    "      if (element) return element;\n",
    "    }\n",
    "    return null;\n",
    "  };\n",
    "  const setValue = (element, value) => {\n",
    "    const prototype = Object.getPrototypeOf(element);\n",
    "    const setter = Object.getOwnPropertyDescriptor(prototype, 'value')?.set;\n",
    "    if (setter) setter.call(element, value);\n",
    "    else element.value = value;\n",
    "    element.dispatchEvent(new Event('input', { bubbles: true }));\n",
    "    element.dispatchEvent(new Event('change', { bubbles: true }));\n",
    "  };\n",
    "  const username = firstVisible([\n",
    "    'input[name=\"username\"]',\n",
    "    'input[autocomplete=\"username\"]',\n",
    "    'input[type=\"text\"]'\n",
    "  ]);\n",
    "  const password = firstVisible([\n",
    "    'input[name=\"password\"]',\n",
    "    'input[autocomplete=\"current-password\"]',\n",
    "    'input[type=\"password\"]'\n",
    "  ]);\n",
    "  if (!username || !password) {\n",
    "    return JSON.stringify({ ok: false, reason: 'Không tìm thấy ô đăng nhập.' });\n",
    "  }\n",
    "  setValue(username, credentials.username);\n",
    "  setValue(password, credentials.password);\n",
    "  const form = password.closest('form') || username.closest('form');\n",
    "  let submit = form?.querySelector('button[type=\"submit\"], input[type=\"submit\"]');\n",
    "  if (!submit) {\n",
    "    submit = Array.from(document.querySelectorAll('button')).find((button) =>\n",
    "      visible(button) && /(log in|đăng nhập)/i.test(button.innerText || '')\n",
    "    );\n",
    "  }\n",
    "  if (!submit) {\n",
    "    return JSON.stringify({ ok: false, reason: 'Không tìm thấy nút Đăng nhập.' });\n",
    "  }\n",
    "  submit.click();\n",
    "  return JSON.stringify({ ok: true, reason: '' });\n",
    "})()"
  )

  jsonlite::fromJSON(
    evaluate_js(browser, expression),
    simplifyVector = TRUE
  )
}

fill_otp_form <- function(browser, otp_code) {
  otp_json <- jsonlite::toJSON(otp_code, auto_unbox = TRUE)

  expression <- paste0(
    "(() => {\n",
    "  const otpCode = ", otp_json, ";\n",
    "  const selectors = [\n",
    "    'input[autocomplete=\"one-time-code\"]',\n",
    "    'input[name=\"verificationCode\"]',\n",
    "    'input[name=\"security_code\"]',\n",
    "    'input[inputmode=\"numeric\"]'\n",
    "  ];\n",
    "  let input = null;\n",
    "  for (const selector of selectors) {\n",
    "    input = document.querySelector(selector);\n",
    "    if (input) break;\n",
    "  }\n",
    "  if (!input) return JSON.stringify({ ok: false });\n",
    "  const setter = Object.getOwnPropertyDescriptor(",
    "Object.getPrototypeOf(input), 'value')?.set;\n",
    "  if (setter) setter.call(input, otpCode);\n",
    "  else input.value = otpCode;\n",
    "  input.dispatchEvent(new Event('input', { bubbles: true }));\n",
    "  input.dispatchEvent(new Event('change', { bubbles: true }));\n",
    "  const form = input.closest('form');\n",
    "  let submit = form?.querySelector('button[type=\"submit\"], input[type=\"submit\"]');\n",
    "  if (!submit) {\n",
    "    submit = Array.from(document.querySelectorAll('button')).find((button) =>\n",
    "      /(confirm|continue|next|xác nhận|tiếp tục)/i.test(button.innerText || '')\n",
    "    );\n",
    "  }\n",
    "  if (submit) submit.click();\n",
    "  return JSON.stringify({ ok: submit !== null });\n",
    "})()"
  )

  jsonlite::fromJSON(
    evaluate_js(browser, expression),
    simplifyVector = TRUE
  )
}

wait_for_login_result <- function(browser, timeout_seconds) {
  started_at <- Sys.time()

  repeat {
    state <- get_page_state(browser)
    form_state <- get_login_form_state(browser)

    if (!needs_login(state)) {
      return("success")
    }

    if (isTRUE(form_state$has_otp)) {
      return("otp")
    }

    if (isTRUE(form_state$has_challenge)) {
      return("challenge")
    }

    elapsed <- as.numeric(
      difftime(Sys.time(), started_at, units = "secs")
    )

    if (elapsed >= timeout_seconds) {
      return("timeout")
    }

    Sys.sleep(1)
  }
}

save_login_cookies <- function(browser) {
  cookies <- browser$Network$getCookies()

  if (is.null(cookies$cookies) || length(cookies$cookies) == 0) {
    stop(
      "Khong lay duoc cookie sau khi dang nhap. Hay thu lai.",
      call. = FALSE
    )
  }

  saveRDS(cookies, cookie_file)
  message("Đã lưu phiên đăng nhập để dùng cho lần chạy sau.")
}

login_from_console <- function(browser) {
  if (!interactive()) {
    stop(
      "Cần chạy file trong RStudio để nhập thông tin đăng nhập ở Console.",
      call. = FALSE
    )
  }

  message("")
  message("ĐĂNG NHẬP TRONG CONSOLE - Chrome đang chạy ẩn.")
  message("Mật khẩu chỉ dùng trong bộ nhớ và không được ghi vào file.")

  navigate_to(browser, "https://www.threads.com/login")
  Sys.sleep(page_load_seconds)

  form_state <- get_login_form_state(browser)

  if (!isTRUE(form_state$has_username) || !isTRUE(form_state$has_password)) {
    stop(
      paste0(
        "Không tìm thấy biểu mẫu đăng nhập Threads. ",
        "Có thể Meta đã thay đổi giao diện hoặc đang yêu cầu xác minh.\n",
        "Hãy đổi login_mode <- \"browser\" và đăng nhập thủ công một lần."
      ),
      call. = FALSE
    )
  }

  username <- trimws(readline(
    prompt = "Tên người dùng, email hoặc số điện thoại Threads/Instagram: "
  ))
  password <- getPass::getPass(msg = "Mật khẩu: ")

  if (!nzchar(username) || !nzchar(password)) {
    password <- NULL
    stop("Tên đăng nhập và mật khẩu không được để trống.", call. = FALSE)
  }

  submit_result <- fill_login_form(browser, username, password)
  password <- NULL
  invisible(gc(verbose = FALSE))

  if (!isTRUE(submit_result$ok)) {
    stop(submit_result$reason, call. = FALSE)
  }

  result <- wait_for_login_result(
    browser,
    console_login_timeout_seconds
  )

  if (identical(result, "otp")) {
    otp_code <- trimws(readline(
      prompt = "Mã xác minh hai bước Meta vừa gửi: "
    ))

    if (!nzchar(otp_code)) {
      stop("Mã xác minh không được để trống.", call. = FALSE)
    }

    otp_result <- fill_otp_form(browser, otp_code)
    otp_code <- NULL

    if (!isTRUE(otp_result$ok)) {
      stop(
        "Không tự điền được mã xác minh. Hãy dùng login_mode <- \"browser\".",
        call. = FALSE
      )
    }

    result <- wait_for_login_result(
      browser,
      console_login_timeout_seconds
    )
  }

  if (identical(result, "challenge")) {
    stop(
      paste0(
        "Meta đang yêu cầu CAPTCHA hoặc xác minh bất thường. ",
        "Phần này không thể xử lý an toàn trong Console.\n",
        "Hãy đổi login_mode <- \"browser\" và xác minh thủ công một lần."
      ),
      call. = FALSE
    )
  }

  if (!identical(result, "success")) {
    stop(
      paste0(
        "Đăng nhập chưa thành công sau ", console_login_timeout_seconds,
        " giây. Có thể sai tài khoản/mật khẩu hoặc Meta đã chặn đăng nhập ẩn.\n",
        "Hãy đổi login_mode <- \"browser\" và đăng nhập thủ công một lần."
      ),
      call. = FALSE
    )
  }

  navigate_to(browser, "https://www.threads.net/")
  Sys.sleep(page_load_seconds)

  if (needs_login(get_page_state(browser))) {
    stop(
      "Threads vẫn yêu cầu đăng nhập. Hãy dùng login_mode <- \"browser\" một lần.",
      call. = FALSE
    )
  }

  save_login_cookies(browser)
  invisible(TRUE)
}

login_interactively <- function(browser) {
  message("")
  message("LẦN ĐẦU: R sẽ mở cửa sổ xem trình duyệt.")
  message("Hãy đăng nhập Threads trong cửa sổ đó. Không nhập mật khẩu vào Console.")

  browser$view()
  navigate_to(browser, "https://www.threads.net/login")

  if (!interactive()) {
    stop(
      "Can chay file trong RStudio de dang nhap Threads thu cong lan dau.",
      call. = FALSE
    )
  }

  readline(
    prompt = paste0(
      "\nSau khi thấy trang chủ Threads và đã đăng nhập, ",
      "quay lại RStudio rồi nhấn Enter: "
    )
  )

  navigate_to(browser, "https://www.threads.net/")
  Sys.sleep(page_load_seconds)

  state <- get_page_state(browser)

  if (needs_login(state)) {
    stop(
      "Threads van hien trang dang nhap. Hay chay lai file va dang nhap xong ",
      "truoc khi nhan Enter.",
      call. = FALSE
    )
  }

  save_login_cookies(browser)
  invisible(TRUE)
}

prepare_login <- function(browser) {
  if (!login_mode %in% c("console", "browser")) {
    stop(
      'login_mode chỉ nhận "console" hoặc "browser".',
      call. = FALSE
    )
  }

  loaded_cookie <- FALSE

  if (file.exists(cookie_file)) {
    saved_cookies <- tryCatch(
      readRDS(cookie_file),
      error = function(e) NULL
    )

    if (!is.null(saved_cookies$cookies) && length(saved_cookies$cookies) > 0) {
      tryCatch(
        {
          browser$Network$setCookies(cookies = saved_cookies$cookies)
          loaded_cookie <- TRUE
        },
        error = function(e) {
          warning(
            "Khong nap duoc phien dang nhap cu: ",
            conditionMessage(e),
            call. = FALSE
          )
        }
      )
    }
  }

  if (!loaded_cookie) {
    if (identical(login_mode, "console")) {
      login_from_console(browser)
    } else {
      login_interactively(browser)
    }
    return(invisible(TRUE))
  }

  navigate_to(browser, "https://www.threads.net/")
  Sys.sleep(page_load_seconds)

  if (needs_login(get_page_state(browser))) {
    message("Phiên đăng nhập cũ đã hết hạn. Hãy đăng nhập lại.")
    if (identical(login_mode, "console")) {
      login_from_console(browser)
    } else {
      login_interactively(browser)
    }
  } else {
    message("Đã dùng lại phiên đăng nhập Threads.")
  }

  invisible(TRUE)
}

build_search_url <- function(keyword, filter = c("recent", "top")) {
  filter <- match.arg(filter)
  url <- paste0(
    "https://www.threads.net/search?q=",
    utils::URLencode(keyword, reserved = TRUE),
    "&serp_type=default"
  )

  if (identical(filter, "top")) {
    url <- paste0(url, "&filter=top")
  }

  url
}

scroll_to_bottom <- function(browser) {
  evaluate_js(
    browser,
    paste(
      c(
        "(() => {",
        "  window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });",
        "  return document.body.scrollHeight;",
        "})()"
      ),
      collapse = "\n"
    )
  )
}

# ----------------------------------------------------------------
# 3. LAY DANH SACH LINK BAI TU TRANG TIM KIEM
# ----------------------------------------------------------------

extract_search_posts_js_v2 <- paste(
  c(
    "(() => {",
    "  const clean = (v) => (v || '').replace(/\\s+/g, ' ').trim();",
    "  const parsePost = (href) => {",
    "    try {",
    "      const parts = new URL(href, location.origin).pathname.split('/').filter(Boolean);",
    "      if (parts.length < 3 || !parts[0].startsWith('@') || parts[1] !== 'post') return null;",
    "      return { post_id: parts[2], nguoi_dang: parts[0].slice(1),",
    "        link: `https://www.threads.net/${parts[0]}/post/${parts[2]}` };",
    "    } catch { return null; }",
    "  };",
    "",
    "  const isActionLabel = (label) =>",
    "    /(like|reply|repost|share|thích|trả lời|đăng lại|chia sẻ)/i.test(label);",
    "",
    "  // Tim card (khoi bai) tu link, dung so nut hanh dong",
    "  const findCard = (link) => {",
    "    let current = link.parentElement;",
    "    for (let d = 0; current && d < 20; d++) {",
    "      const buttons = Array.from(current.querySelectorAll('[role=\"button\"]'));",
    "      const labels = buttons.map((b) => clean(`${b.getAttribute('aria-label')||''} ${b.textContent||''}`));",
    "      if (labels.filter(isActionLabel).length >= 2) return current;",
    "      current = current.parentElement;",
    "    }",
    "    return null;",
    "  };",
    "",
    "  // Dem so reply tu cac nut hanh dong",
    "  const getReplyCount = (card) => {",
    "    if (!card) return 0;",
    "    for (const btn of card.querySelectorAll('[role=\"button\"]')) {",
    "      const label = clean(`${btn.getAttribute('aria-label')||''} ${btn.textContent||''}`);",
    "      const match = label.match(/(\\d+)\\s*(repl|comment|trả lời|bình luận)/i);",
    "      if (match) return parseInt(match[1], 10);",
    "      const match2 = label.match(/(repl|comment|trả lời|bình luận).*?(\\d+)/i);",
    "      if (match2) return parseInt(match2[2], 10);",
    "    }",
    "    // Kiem tra text 'No replies yet'",
    "    if (/no replies yet/i.test(card.innerText)) return 0;",
    "    return -1;",
    "  };",
    "",
    "  // Lay noi dung tom tat cua bai",
    "  const getSnippet = (card) => {",
    "    if (!card) return '';",
    "    const texts = [];",
    "    for (const el of card.querySelectorAll('div[dir=\"auto\"], span[dir=\"auto\"]')) {",
    "      const text = clean(el.textContent);",
    "      if (!text || text.length < 3) continue;",
    "      if (/^(like|reply|repost|share|translate|thích|trả lời|đăng lại|chia sẻ|dịch)$/i.test(text)) continue;",
    "      if (/^[0-9.,]+$/.test(text)) continue;",
    "      const nested = el.querySelector('[dir=\"auto\"]');",
    "      if (nested && clean(nested.textContent) === text) continue;",
    "      texts.push(text);",
    "      if (texts.length >= 3) break;",
    "    }",
    "    return texts.join(' ').substring(0, 300);",
    "  };",
    "",
    "  let links = Array.from(document.querySelectorAll('time'))",
    "    .map((time) => time.closest('a[href*=\"/post/\"]'))",
    "    .filter(Boolean);",
    "",
    "  if (links.length === 0) {",
    "    links = Array.from(document.querySelectorAll('a[href*=\"/post/\"]'));",
    "  }",
    "",
    "  const seen = new Set();",
    "  const output = [];",
    "",
    "  for (const link of links) {",
    "    const parsed = parsePost(link.getAttribute('href') || '');",
    "    if (!parsed || seen.has(parsed.post_id)) continue;",
    "    seen.add(parsed.post_id);",
    "    const card = findCard(link);",
    "    parsed.noi_dung_tom_tat = getSnippet(card);",
    "    parsed.so_reply = getReplyCount(card);",
    "    output.push(parsed);",
    "  }",
    "",
    "  return JSON.stringify(output);",
    "})()"
  ),
  collapse = "\n"
)

extract_search_posts_from_page <- function(browser) {
  posts_json <- evaluate_js(browser, extract_search_posts_js_v2)

  if (is.null(posts_json) || !nzchar(posts_json) || identical(posts_json, "[]")) {
    return(empty_search_posts())
  }

  parsed <- jsonlite::fromJSON(posts_json, simplifyDataFrame = TRUE)

  if (!is.data.frame(parsed) || nrow(parsed) == 0) {
    return(empty_search_posts())
  }

  as_tibble(parsed) |>
    transmute(
      post_id = as.character(post_id),
      nguoi_dang = as.character(nguoi_dang),
      link = as.character(link),
      noi_dung_tom_tat = as.character(
        if ("noi_dung_tom_tat" %in% names(parsed)) noi_dung_tom_tat else ""
      ),
      so_reply = as.integer(
        if ("so_reply" %in% names(parsed)) so_reply else -1L
      )
    ) |>
    filter(nzchar(post_id), nzchar(link)) |>
    distinct(post_id, .keep_all = TRUE)
}

# ----------------------------------------------------------------
# 4. TRANG CHI TIET BAI: TACH BAI GOC VA CAC COMMENT (DA SUA)
# ----------------------------------------------------------------
# Ky thuat: voi moi link ho so tac gia (a[href^="/@..."]), tro len tim
# khoi (card) gan nhat co >= 2 nut hanh dong (like/reply/repost...).
# Day la cach da chung minh hoat dong tot o trang tim kiem
# (xem findContainer trong ban goc). Dung <time> lam diem bat dau de
# tim card rat de bi nhieu comment cung tro len trung mot node cha,
# khien comments bi rong - day la nguyen nhan loi ban gap phai.

extract_post_detail_js <- paste(
  c(
    "(() => {",
    "  const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();",
    "  const rootParts = location.pathname.split('/').filter(Boolean);",
    "  const rootPostId = rootParts.length >= 3 && rootParts[1] === 'post' ? rootParts[2] : '';",
    "  const rootUsername = (rootParts[0] || '').replace('@', '');",
    "",
    "  const metaDesc = document.querySelector('meta[property=\"og:description\"]');",
    "  let mainContent = clean(metaDesc?.getAttribute('content') || '');",
    "  if (mainContent.toLocaleLowerCase().startsWith((rootUsername + ':').toLocaleLowerCase())) {",
    "    mainContent = mainContent.substring(rootUsername.length + 1).trim();",
    "  }",
    "",
    "  const isActionLabel = (label) =>",
    "    /(like|reply|repost|share|thích|trả lời|đăng lại|chia sẻ|讚|留言|回覆|轉發|分享)/i.test(label);",
    "",
    "  const countActions = (node) => {",
    "    const roleButtons = Array.from(node.querySelectorAll('[role=\"button\"]'));",
    "    const labels = roleButtons.map((button) =>",
    "      clean(`${button.getAttribute('aria-label') || ''} ${button.textContent || ''}`)",
    "    );",
    "    return labels.filter(isActionLabel).length;",
    "  };",
    "",
    "  // Tim khoi (card) chua dung 1 bai/1 comment, bat dau tu link tac gia,",
    "  // dung so nut hanh dong de xac dinh bien cua card (giong logic o",
    "  // trang tim kiem, thay vi dua vao <time>).",
    "  const uniquePostLinks = (node) => {",
    "    const ids = new Set();",
    "    for (const link of node.querySelectorAll('a[href*=\"/post/\"]')) {",
    "      try {",
    "        const parts = new URL(link.href, location.origin).pathname.split('/').filter(Boolean);",
    "        if (parts.length >= 3 && parts[1] === 'post') ids.add(parts[2]);",
    "      } catch {}",
    "    }",
    "    return ids.size;",
    "  };",
    "",
    "  const findCard = (userLink) => {",
    "    let current = userLink.parentElement;",
    "    let fallback = null;",
    "    for (let depth = 0; current && depth < 20; depth += 1) {",
    "      const text = clean(current.innerText);",
    "      if (text.length > 0 && !fallback) fallback = current;",
    "      // Dung leo len neu container co nhieu bai/comment khac nhau",
    "      if (uniquePostLinks(current) > 2) return fallback;",
    "      if (countActions(current) >= 2) return current;",
    "      current = current.parentElement;",
    "    }",
    "    return fallback;",
    "  };",
    "",
    "  const isNoise = (text, username) => {",
    "    const lower = text.toLocaleLowerCase();",
    "    if (!text) return true;",
    "    if (lower === username.toLocaleLowerCase() || lower === `@${username.toLocaleLowerCase()}`) return true;",
    "    if (/^[0-9.,]+$/.test(text)) return true;",
    "    if (/^[0-9]+\\s*(s|m|h|d|w|y|giây|phút|giờ|ngày|tuần|tháng|năm)(\\s*trước)?$/i.test(text)) return true;",
    "    if (/^(like|comment|reply|repost|share|translate|see translation|thích|bình luận|trả lời|đăng lại|chia sẻ|dịch|xem bản dịch|views|lượt xem)$/i.test(text)) return true;",
    "    if (/^(reply to|replying to|đang trả lời|trả lời)\\s+@?/i.test(text)) return true;",
    "    if (/^(log in|sign up|đăng nhập|đăng ký)$/i.test(text)) return true;",
    "    if (/^(feeds|no replies yet|some replies|see all|learn more)$/i.test(text)) return true;",
    "    if (/(terms|privacy policy|cookies policy|© 20)/i.test(text)) return true;",
    "    if (/^(author|· author)$/i.test(text)) return true;",
    "    if (/^\\d{1,2}\\/\\d{1,2}\\/\\d{2,4}$/i.test(text)) return true;",
    "    return false;",
    "  };",
    "",
    "  // Chi lay link ho so dang '/@username' (khong lay link post/comment).",
    "  const userLinks = Array.from(document.querySelectorAll('a[href^=\"/@\"]'))",
    "    .filter((link) => /^\\/@[^/?#]+\\/?$/.test(link.getAttribute('href') || ''));",
    "",
    "  const seenCards = [];",
    "  const cards = [];",
    "",
    "  for (const link of userLinks) {",
    "    const card = findCard(link);",
    "    if (!card) continue;",
    "    // Bo qua neu card nay la to tien/con chau cua mot card da lay,",
    "    // de tranh trung lap khi nhieu link tac gia roi vao cung 1 khoi.",
    "    const overlaps = seenCards.some(",
    "      (existing) => existing === card || existing.contains(card) || card.contains(existing)",
    "    );",
    "    if (overlaps) continue;",
    "",
    "    const username = clean(link.textContent).replace(/^@/, '') || rootUsername;",
    "    const textEls = card.querySelectorAll('div[dir=\"auto\"], span[dir=\"auto\"]');",
    "    const texts = [];",
    "    const textSeen = new Set();",
    "",
    "    for (const el of textEls) {",
    "      const text = clean(el.textContent);",
    "      if (isNoise(text, username)) continue;",
    "      const nested = el.querySelector('[dir=\"auto\"]');",
    "      if (nested && clean(nested.textContent) === text) continue;",
    "      if (textSeen.has(text)) continue;",
    "      textSeen.add(text);",
    "      texts.push(text);",
    "    }",
    "",
    "    const content = texts.join(' ').trim();",
    "    if (!content) continue;",
    "",
    "    const timeEl = card.querySelector('time');",
    "    const timestamp = timeEl",
    "      ? (timeEl.getAttribute('datetime') || clean(timeEl.textContent))",
    "      : '';",
    "",
    "    seenCards.push(card);",
    "    cards.push({ username, content, timestamp });",
    "  }",
    "",
    "  let post = null;",
    "  const comments = [];",
    "",
    "  for (const card of cards) {",
    "    const sameAsMain = mainContent.length > 0 && card.content === mainContent;",
    "    if (!post && (card.username.toLocaleLowerCase() === rootUsername.toLocaleLowerCase() || sameAsMain)) {",
    "      post = {",
    "        post_id: rootPostId,",
    "        nguoi_dang: card.username || rootUsername,",
    "        noi_dung: card.content || mainContent,",
    "        thoi_gian_dang: card.timestamp,",
    "        link: location.href,",
    "        so_luot_thich: 0,",
    "        so_luot_tra_loi: 0",
    "      };",
    "    } else {",
    "      comments.push({",
    "        comment_id: '',",
    "        nguoi_comment: card.username,",
    "        noi_dung_comment: card.content,",
    "        thoi_gian_comment: card.timestamp,",
    "        so_luot_thich_comment: 0,",
    "        so_luot_tra_loi_comment: 0,",
    "        dang_tra_loi: '',",
    "        link_comment: ''",
    "      });",
    "    }",
    "  }",
    "",
    "  if (!post) {",
    "    post = {",
    "      post_id: rootPostId,",
    "      nguoi_dang: rootUsername,",
    "      noi_dung: mainContent,",
    "      thoi_gian_dang: '',",
    "      link: location.href,",
    "      so_luot_thich: 0,",
    "      so_luot_tra_loi: comments.length",
    "    };",
    "  }",
    "",
    "  return JSON.stringify({ post, comments });",
    "})()"
  ),
  collapse = "\n"
)

expand_comments_js <- paste(
  c(
    "(() => {",
    "  const visible = (element) => {",
    "    const style = getComputedStyle(element);",
    "    const rect = element.getBoundingClientRect();",
    "    return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;",
    "  };",
    "  const pattern = /(view|see|show|load).*(repl|comment)|(xem|hiện|tải).*(câu trả lời|phản hồi|bình luận)/i;",
    "  const buttons = Array.from(document.querySelectorAll('button, [role=\"button\"]'))",
    "    .filter((button) => visible(button))",
    "    .filter((button) => pattern.test(`${button.getAttribute('aria-label') || ''} ${button.textContent || ''}`));",
    "  let clicked = 0;",
    "  for (const button of buttons.slice(0, 6)) {",
    "    try { button.click(); clicked += 1; } catch {}",
    "  }",
    "  window.scrollBy({ top: Math.max(window.innerHeight * 0.9, 600), behavior: 'smooth' });",
    "  return clicked;",
    "})()"
  ),
  collapse = "\n"
)

extract_post_detail_from_page <- function(browser) {
  detail_json <- evaluate_js(browser, extract_post_detail_js)

  if (is.null(detail_json) || !nzchar(detail_json)) {
    return(list(
      post = empty_posts_output(),
      comments = empty_comments_output()
    ))
  }

  parsed <- jsonlite::fromJSON(detail_json, simplifyDataFrame = TRUE)

  if (is.null(parsed$post)) {
    return(list(
      post = empty_posts_output(),
      comments = empty_comments_output()
    ))
  }

  post_raw <- as_tibble(parsed$post)
  content <- as.character(post_raw$noi_dung[[1]])
  crawled_at <- format(Sys.time(), "%Y-%m-%d %H:%M:%S %z")
  title <- make_post_title(content)

  post <- post_raw |>
    transmute(
      post_id = as.character(post_id),
      tieu_de_bai_viet = title,
      nguoi_dang = as.character(nguoi_dang),
      noi_dung_bai_viet = as.character(noi_dung),
      thoi_gian_dang = as.character(thoi_gian_dang),
      link_bai_viet = as.character(link),
      so_luot_thich = as.numeric(so_luot_thich),
      so_luot_tra_loi = as.numeric(so_luot_tra_loi),
      thoi_gian_cao = crawled_at
    )

  if (is.null(parsed$comments) || length(parsed$comments) == 0) {
    return(list(post = post, comments = empty_comments_output()))
  }

  comments_raw <- as_tibble(parsed$comments)

  if (nrow(comments_raw) == 0) {
    return(list(post = post, comments = empty_comments_output()))
  }

  comments <- comments_raw |>
    transmute(
      post_id = post$post_id[[1]],
      tieu_de_bai_viet = post$tieu_de_bai_viet[[1]],
      link_bai_viet = post$link_bai_viet[[1]],
      nguoi_comment = as.character(nguoi_comment),
      noi_dung_comment = clean_comment_content(noi_dung_comment),
      thoi_gian_comment = as.character(thoi_gian_comment),
      so_luot_thich_comment = as.numeric(so_luot_thich_comment),
      so_luot_tra_loi_comment = as.numeric(so_luot_tra_loi_comment),
      dang_tra_loi = as.character(dang_tra_loi),
      link_comment = as.character(link_comment),
      thoi_gian_cao = crawled_at
    ) |>
    filter(nzchar(noi_dung_comment)) |>
    # comment_id tinh theo noi dung + nguoi comment, KHONG theo thu tu,
    # de dedup dung qua nhieu vong load them comment.
    mutate(
      comment_id = paste0(nguoi_comment, "::", substr(noi_dung_comment, 1, 80))
    ) |>
    relocate(comment_id, .after = link_bai_viet)

  list(post = post, comments = comments)
}

collect_search_posts <- function(browser, group, keyword) {
  search_url <- build_search_url(keyword, search_filter)
  message("")
  message("Đang tìm [", group, "] - ", keyword)

  navigate_to(browser, search_url)
  Sys.sleep(page_load_seconds)

  state <- get_page_state(browser)

  if (needs_login(state)) {
    message("Threads yêu cầu đăng nhập lại.")
    if (identical(login_mode, "console")) {
      login_from_console(browser)
    } else {
      login_interactively(browser)
    }
    navigate_to(browser, search_url)
    Sys.sleep(page_load_seconds)
  }

  collected <- empty_search_posts()
  stagnant_rounds <- 0L
  previous_count <- 0L

  for (scroll_number in 0:max_scrolls_per_keyword) {
    visible_posts <- tryCatch(
      extract_search_posts_from_page(browser),
      error = function(e) {
        warning(
          "Khong doc duoc ket qua cho tu khoa '", keyword, "': ",
          conditionMessage(e),
          call. = FALSE
        )
        empty_search_posts()
      }
    )

    collected <- bind_rows(collected, visible_posts) |>
      distinct(post_id, .keep_all = TRUE)

    message(
      "  Lần cuộn ", scroll_number, "/", max_scrolls_per_keyword,
      " - tìm thấy ", nrow(collected), " link bài"
    )

    if (nrow(collected) >= max_posts_per_keyword) {
      break
    }

    if (nrow(collected) == previous_count) {
      stagnant_rounds <- stagnant_rounds + 1L
    } else {
      stagnant_rounds <- 0L
    }

    if (stagnant_rounds >= 3L) {
      break
    }

    previous_count <- nrow(collected)
    scroll_to_bottom(browser)
    sleep_random(scroll_pause_min, scroll_pause_max)
  }

  collected |>
    slice_head(n = max_posts_per_keyword)
}

crawl_post_and_top_comments <- function(browser, post_link) {
  navigate_to(browser, post_link)
  Sys.sleep(page_load_seconds)

  if (needs_login(get_page_state(browser))) {
    message("Threads yêu cầu đăng nhập lại.")
    if (identical(login_mode, "console")) {
      login_from_console(browser)
    } else {
      login_interactively(browser)
    }
    navigate_to(browser, post_link)
    Sys.sleep(page_load_seconds)
  }

  best_post <- empty_posts_output()
  collected_comments <- empty_comments_output()
  stagnant_rounds <- 0L
  previous_count <- 0L

  for (round_number in 0:max_comment_load_rounds) {
    detail <- extract_post_detail_from_page(browser)

    if (nrow(detail$post) > 0) {
      best_post <- detail$post
    }

    collected_comments <- bind_rows(
      collected_comments,
      detail$comments
    ) |>
      distinct(comment_id, .keep_all = TRUE)

    message(
      "    Comment nổi bật: ",
      min(nrow(collected_comments), max_comments_per_post),
      "/", max_comments_per_post
    )

    if (nrow(collected_comments) >= max_comments_per_post) {
      break
    }

    if (nrow(collected_comments) == previous_count) {
      stagnant_rounds <- stagnant_rounds + 1L
    } else {
      stagnant_rounds <- 0L
    }

    if (stagnant_rounds >= 3L) {
      break
    }

    previous_count <- nrow(collected_comments)
    evaluate_js(browser, expand_comments_js)
    sleep_random(scroll_pause_min, scroll_pause_max)
  }

  comments <- collected_comments |>
    slice_head(n = max_comments_per_post)

  list(post = best_post, comments = comments)
}

# ----------------------------------------------------------------
# 5. VONG LAP CHINH: CHI XUAT 1 FILE COMMENT
# ----------------------------------------------------------------

run_threads_crawler <- function() {
  dir.create(output_dir, recursive = TRUE, showWarnings = FALSE)

  if (!dir.exists(output_dir)) {
    stop("Khong tao duoc thu muc ket qua: ", output_dir, call. = FALSE)
  }

  if (file.access(output_dir, mode = 2) != 0) {
    stop("Khong co quyen ghi vao thu muc: ", output_dir, call. = FALSE)
  }

  normalized_output_dir <- normalizePath(
    output_dir,
    winslash = "/",
    mustWork = TRUE
  )

  # --- Dung chung file thread_city.csv, append ket qua moi ---
  comments_csv <- file.path(normalized_output_dir, "thread_city.csv")

  run_id <- format(Sys.time(), "%Y%m%d_%H%M%S")
  debug_file <- file.path(
    normalized_output_dir,
    paste0("threads_debug_", run_id, ".png")
  )

  all_posts <- empty_posts_output()
  all_comments <- empty_comments_output()

  # Doc thread_city.csv cu de lay danh sach post_id da crawl truoc do
  existing_post_ids <- character()
  if (file.exists(comments_csv)) {
    existing_data <- tryCatch(
      readr::read_csv(comments_csv, col_types = readr::cols(.default = "c"),
                      show_col_types = FALSE),
      error = function(e) NULL
    )
    if (!is.null(existing_data) && "post_id" %in% names(existing_data)) {
      existing_post_ids <- unique(existing_data$post_id)
      message("Đã đọc thread_city.csv: ", nrow(existing_data), " dòng, ",
              length(existing_post_ids), " post_id đã có.")
    }
  }
  crawled_post_ids <- existing_post_ids

  options(chromote.timeout = 180)

  browser <- tryCatch(
    chromote::ChromoteSession$new(),
    error = function(e) {
      stop(
        "Khong mo duoc Chrome. Hay cai/cap nhat Google Chrome, sau do chay lai.\n",
        "Chi tiet: ", conditionMessage(e),
        call. = FALSE
      )
    }
  )

  on.exit(
    try(browser$close(), silent = TRUE),
    add = TRUE
  )

  prepare_login(browser)

  # Tao file thread_city.csv voi header neu chua ton tai
  if (!file.exists(comments_csv)) {
    readr::write_excel_csv(comments_for_csv(all_comments), comments_csv, na = "")
  }

  message("")
  message("Kết quả sẽ được ghi nối vào: ", comments_csv)
  message("- Số post_id đã có (sẽ bỏ qua): ", length(existing_post_ids))

  for (keyword_index in seq_len(nrow(keyword_dictionary))) {
    group <- keyword_dictionary$nhom[[keyword_index]]
    keyword <- keyword_dictionary$tu_khoa[[keyword_index]]

    search_posts <- tryCatch(
      collect_search_posts(browser, group, keyword),
      error = function(e) {
        warning(
          "Bo qua tu khoa '", keyword, "': ",
          conditionMessage(e),
          call. = FALSE
        )
        empty_search_posts()
      }
    )

    if (nrow(search_posts) == 0) {
      next
    }

    for (post_index in seq_len(nrow(search_posts))) {
      post_id <- search_posts$post_id[[post_index]]
      post_link <- search_posts$link[[post_index]]
      snippet <- search_posts$noi_dung_tom_tat[[post_index]]
      reply_count <- search_posts$so_reply[[post_index]]

      if (post_id %in% crawled_post_ids) {
        message(
          "  Bài ", post_index, "/", nrow(search_posts),
          " đã có trong thread_city.csv, bỏ qua."
        )
        next
      }

      # Loc chi bai tieng Viet
      if (isTRUE(only_vietnamese) && nzchar(snippet) && !is_vietnamese_text(snippet)) {
        message(
          "  Bài ", post_index, "/", nrow(search_posts),
          " không phải tiếng Việt, bỏ qua."
        )
        next
      }

      # Loc chi bai co comment
      if (isTRUE(only_posts_with_comments) && identical(as.integer(reply_count), 0L)) {
        message(
          "  Bài ", post_index, "/", nrow(search_posts),
          " không có comment, bỏ qua."
        )
        next
      }

      message(
        "  Đang cào bài ", post_index, "/", nrow(search_posts),
        ": ", post_link
      )

      detail <- tryCatch(
        crawl_post_and_top_comments(browser, post_link),
        error = function(e) {
          warning(
            "Bo qua bai '", post_link, "': ",
            conditionMessage(e),
            call. = FALSE
          )
          list(
            post = empty_posts_output(),
            comments = empty_comments_output()
          )
        }
      )

      if (nrow(detail$post) > 0) {
        all_posts <- bind_rows(all_posts, detail$post) |>
          distinct(post_id, .keep_all = TRUE)

        all_comments <- bind_rows(all_comments, detail$comments) |>
          distinct(post_id, comment_id, .keep_all = TRUE) |>
          group_by(post_id) |>
          slice_head(n = max_comments_per_post) |>
          ungroup()

        crawled_post_ids <- unique(c(crawled_post_ids, post_id))
      }

      # Append comment moi vao thread_city.csv (ghi noi, khong ghi de)
      new_comments_for_post <- detail$comments
      if (nrow(new_comments_for_post) > 0) {
        readr::write_csv(
          comments_for_csv(new_comments_for_post),
          comments_csv,
          append = TRUE,
          col_names = FALSE,
          na = ""
        )
      }

      if (post_index < nrow(search_posts)) {
        sleep_random(post_pause_min, post_pause_max)
      }
    }

    if (keyword_index < nrow(keyword_dictionary)) {
      sleep_random(keyword_pause_min, keyword_pause_max)
    }
  }

  if (nrow(all_comments) == 0) {
    try(browser$screenshot(debug_file), silent = TRUE)

    warning(
      paste0(
        "Khong cao duoc comment nao. Anh chup man hinh de kiem tra da luu tai: ",
        debug_file, "\n",
        "Neu van gap loi nay, hay chia se anh do de kiem tra lai cau truc trang."
      ),
      call. = FALSE
    )
  }

  # Doc lai thread_city.csv de dem tong so dong sau khi append
  final_count <- tryCatch(
    nrow(readr::read_csv(comments_csv, col_types = readr::cols(.default = "c"),
                         show_col_types = FALSE)),
    error = function(e) NA_integer_
  )

  cat(
    "\nHoàn tất!\n",
    "- Số bài mới cào lần này: ", nrow(all_posts), "\n",
    "- Số comment mới lần này: ", nrow(all_comments), "\n",
    "- Tổng số dòng trong thread_city.csv: ", final_count, "\n",
    "- Tối đa comment mỗi bài: ", max_comments_per_post, "\n",
    "- File: ", comments_csv, "\n",
    sep = ""
  )

  invisible(comments_for_csv(all_comments))
}

run_threads_crawler()
