# Custom theme JSON

FedOps Agent Studio의 사용자 테마는 제품에 포함되는 기본 테마가 아니다. 로그인한 사용자가
Settings에서 로컬 JSON 파일을 가져오면 검증된 내용만 현재 FedOps 계정의 browser
localStorage에 저장된다. FedOps Web 또는 Studio API로 파일을 업로드하지 않는다.

## 사용 방법

1. 새 테마를 만들 때는 원하는 Light, Dark 또는 사용자 테마를 먼저 적용한다.
2. `Download baseline JSON`으로 현재 색상 token이 담긴 `fedops-theme-baseline.json`을 받는다.
3. 파일의 `id`, `name`과 색상을 수정한 뒤 `Import theme JSON…`을 선택한다.
4. 검증된 테마는 즉시 선택·적용되며 앱을 다시 열어도 유지된다.
5. 같은 `id`의 파일을 다시 가져오면 확인 후 기존 테마를 교체한다.
6. `Remove`는 현재 계정의 브라우저 저장소에서만 테마를 삭제한다. 사용 중인 테마를
   삭제하면 Light로 돌아간다.

Baseline은 별도 테마 palette를 제품에 포함하지 않고, 다운로드 시점에 실제 적용된 UI,
Terminal과 semantic editor 색상을 JSON v1으로 정규화해서 만든다. 새 테마로 가져오기 전에
기본 `my-custom-theme`, `My Custom Theme` 값을 고유한 `id`와 이름으로 변경하는 것이 좋다.

## v1 계약

루트 필드는 다음과 같다.

- `schemaVersion`: 숫자 `1`
- `id`: 소문자·숫자·하이픈으로 된 2–48자 안정 ID
- `name`: UI에 표시할 1–60자 이름
- `appearance`: `dark` 또는 `light`
- `ui`: Studio chrome과 일반 component의 semantic colors
- `terminal`: xterm surface와 ANSI 16 colors
- `editor`: Monaco surface와 syntax token colors

모든 색상은 `#RRGGBB` 또는 `#RRGGBBAA` 형식이어야 하며, CSS expression이나 URL은
허용하지 않는다. 최대 파일 크기는 64 KB다.

필수 `ui` 키:

```text
background, chrome, surface, surfaceAlt, surfaceRaised,
border, borderSubtle, control, input, text, muted, dim,
accent, success, warning, error, purple, orange, brand,
primaryText, selection, focus, scrollbar, scrollbarHover,
shadow, overlay
```

필수 `terminal` 키:

```text
background, surface, input, border, foreground, muted, dim,
accent, prompt, cursor, selection
```

`terminal.ansi`에는 `black`, `red`, `green`, `yellow`, `blue`, `magenta`, `cyan`, `white`와
각각의 `brightBlack`부터 `brightWhite`까지 16개 키가 필요하다.

필수 `editor` 키:

```text
background, foreground, lineHighlight, selection, selectionHighlight,
comment, red, orange, yellow, green, cyan, purple, pink
```

앱은 이 semantic contract만 제공하며 특정 외부 테마의 실제 색상 값이나 자동 설치 파일을
포함하지 않는다.
