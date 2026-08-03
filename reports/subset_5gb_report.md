# 5GB 축소 데이터셋 생성 보고서

## 1. 경로와 형식

| 항목 | 값 |
| --- | --- |
| 원본 경로 | `/Users/a1/Desktop/Hackton/Contextory_AI/data/hf_source/github-codereview` |
| 출력 경로 | `/Users/a1/Desktop/Hackton/Contextory_AI/data/subset_5gb` |
| 감지된 구조 | `record` |
| 형식 구성 | parquet x9, aux x1 |
| 사용 시드 | `42` |

## 2. 용량 비교

| 항목 | 값 |
| --- | --- |
| 원본 전체 용량 | 652.89 MB (652,892,020 bytes) |
| 축소 전체 용량 | 431.51 MB (431,506,900 bytes) |
| 축소 비율 | 66.09% (원본 대비) |
| 목표 용량 | 4.80 GB |
| 상한 용량 | 5.00 GB (5,000,000,000 bytes) |
| 상한 충족 | 충족 (<= 상한) |
| 추가 축소 라운드 | 0 |

원본 내역: 레코드 파일 652.89 MB / 미디어 0.00 B
/ 부속 파일 5.42 KB / 기타 0.00 B

압축은 적용하지 않았습니다(압축 전 = 압축 후 = 위 표의 축소 전체 용량).

## 3. 샘플 수 비교

| 항목 | 원본 | 축소 | 비율 |
| --- | ---: | ---: | ---: |
| 레코드 | 237,877 | 237,877 | 100.00% |
| 미디어 파일 | 0 | 0 | 0.00% |
| 부속 파일 | 1 | 1 | - |

## 4. split 별 비교

| split | 원본 건수 | 축소 건수 | 원본 비율 | 축소 비율 |
| --- | ---: | ---: | ---: | ---: |
| `test` | 11,013 | 11,013 | 4.63% | 4.63% |
| `train` | 219,394 | 219,394 | 92.23% | 92.23% |
| `validation` | 7,470 | 7,470 | 3.14% | 3.14% |

## 5. 라벨/클래스 분포 비교

| 값 | 원본 건수 | 원본 비율 | 축소 건수 | 축소 비율 | 비율 차이 |
| --- | ---: | ---: | ---: | ---: | ---: |
| `suggestion` | 111,737 | 46.97% | 111,737 | 46.97% | +0.00pp |
| `none` | 52,536 | 22.09% | 52,536 | 22.09% | +0.00pp |
| `question` | 32,503 | 13.66% | 32,503 | 13.66% | +0.00pp |
| `bug` | 15,029 | 6.32% | 15,029 | 6.32% | +0.00pp |
| `refactor` | 10,099 | 4.25% | 10,099 | 4.25% | +0.00pp |
| `performance` | 5,207 | 2.19% | 5,207 | 2.19% | +0.00pp |
| `style` | 3,658 | 1.54% | 3,658 | 1.54% | +0.00pp |
| `security` | 3,644 | 1.53% | 3,644 | 1.53% | +0.00pp |
| `nitpick` | 3,464 | 1.46% | 3,464 | 1.46% | +0.00pp |

## 6. 메타데이터 분포 비교

| 값 | 원본 건수 | 원본 비율 | 축소 건수 | 축소 비율 | 비율 차이 |
| --- | ---: | ---: | ---: | ---: | ---: |
| `Python` | 56,631 | 23.81% | 56,631 | 23.81% | +0.00pp |
| `TypeScript` | 32,085 | 13.49% | 32,085 | 13.49% | +0.00pp |
| `Rust` | 28,863 | 12.13% | 28,863 | 12.13% | +0.00pp |
| `Go` | 26,791 | 11.26% | 26,791 | 11.26% | +0.00pp |
| `C++` | 24,272 | 10.20% | 24,272 | 10.20% | +0.00pp |
| `JavaScript` | 15,406 | 6.48% | 15,406 | 6.48% | +0.00pp |
| `C#` | 9,481 | 3.99% | 9,481 | 3.99% | +0.00pp |
| `Java` | 7,950 | 3.34% | 7,950 | 3.34% | +0.00pp |
| `C/C++` | 7,251 | 3.05% | 7,251 | 3.05% | +0.00pp |
| `Kotlin` | 5,125 | 2.15% | 5,125 | 2.15% | +0.00pp |
| `C` | 4,189 | 1.76% | 4,189 | 1.76% | +0.00pp |
| `Swift` | 4,179 | 1.76% | 4,179 | 1.76% | +0.00pp |
| `PHP` | 2,901 | 1.22% | 2,901 | 1.22% | +0.00pp |
| `Vue` | 2,081 | 0.87% | 2,081 | 0.87% | +0.00pp |
| `Shell` | 1,980 | 0.83% | 1,980 | 0.83% | +0.00pp |
| `Ruby` | 1,654 | 0.70% | 1,654 | 0.70% | +0.00pp |
| `Lua` | 1,331 | 0.56% | 1,331 | 0.56% | +0.00pp |
| `Julia` | 808 | 0.34% | 808 | 0.34% | +0.00pp |
| `PowerShell` | 766 | 0.32% | 766 | 0.32% | +0.00pp |
| `Objective-C` | 613 | 0.26% | 613 | 0.26% | +0.00pp |
| `CUDA` | 510 | 0.21% | 510 | 0.21% | +0.00pp |
| `Scala` | 496 | 0.21% | 496 | 0.21% | +0.00pp |
| `Dart` | 470 | 0.20% | 470 | 0.20% | +0.00pp |
| `Svelte` | 452 | 0.19% | 452 | 0.19% | +0.00pp |
| `Objective-C++` | 425 | 0.18% | 425 | 0.18% | +0.00pp |
| `ClojureScript` | 320 | 0.13% | 320 | 0.13% | +0.00pp |
| `Haskell` | 244 | 0.10% | 244 | 0.10% | +0.00pp |
| `SQL` | 203 | 0.09% | 203 | 0.09% | +0.00pp |
| `Protocol Buffers` | 185 | 0.08% | 185 | 0.08% | +0.00pp |
| `Solidity` | 98 | 0.04% | 98 | 0.04% | +0.00pp |
| `Assembly` | 40 | 0.02% | 40 | 0.02% | +0.00pp |
| `R` | 24 | 0.01% | 24 | 0.01% | +0.00pp |
| `Elixir` | 15 | 0.01% | 15 | 0.01% | +0.00pp |
| `GraphQL` | 13 | 0.01% | 13 | 0.01% | +0.00pp |
| `Zig` | 12 | 0.01% | 12 | 0.01% | +0.00pp |
| `Clojure` | 8 | 0.00% | 8 | 0.00% | +0.00pp |
| `F#` | 5 | 0.00% | 5 | 0.00% | +0.00pp |

## 6-1. 미디어 클래스 분포 비교

미디어 파일이 없습니다.

## 7. 파일별 선택 결과

| 원본 파일 | 형식 | split | 원본 레코드 | 선택 레코드 |
| --- | --- | --- | ---: | ---: |
| `data/test/test-00000-of-00001.parquet` | parquet | `test` | 11,013 | 11,013 |
| `data/train/train-00000-of-00003.parquet` | parquet | `train` | 48,240 | 48,240 |
| `data/train/train-00000-of-00004.parquet` | parquet | `train` | 19,704 | 19,704 |
| `data/train/train-00001-of-00003.parquet` | parquet | `train` | 47,872 | 47,872 |
| `data/train/train-00001-of-00004.parquet` | parquet | `train` | 23,017 | 23,017 |
| `data/train/train-00002-of-00003.parquet` | parquet | `train` | 36,622 | 36,622 |
| `data/train/train-00002-of-00004.parquet` | parquet | `train` | 18,807 | 18,807 |
| `data/train/train-00003-of-00004.parquet` | parquet | `train` | 25,132 | 25,132 |
| `data/validation/validation-00000-of-00001.parquet` | parquet | `validation` | 7,470 | 7,470 |

## 8. 데이터 품질

| 항목 | 값 |
| --- | --- |
| 파싱 실패로 제외한 행 | 0 |
| 손상/미해석 파일 | 0 |
| 중복 검사 방식 | 레코드 내용 지문(blake2b-128, 키 정렬·수집시각 제외)으로 파일 간 중복까지 검사 |
| 내용 중복으로 제외한 레코드 | 117,930 |
| 재로딩 가능 | 예 |
| 원본과 스키마 일치 | 예 |
| 검증한 출력 파일 수 | 9 |

## 9. 개인정보 점검

- data/test/test-00000-of-00001.parquet: 컬럼명 'author_username' 이 개인정보 관련으로 보입니다.
- data/test/test-00000-of-00001.parquet: 컬럼명 'reviewer_username' 이 개인정보 관련으로 보입니다.
- data/train/train-00000-of-00003.parquet: 컬럼명 'reviewer_username' 이 개인정보 관련으로 보입니다.
- data/train/train-00000-of-00003.parquet: 컬럼명 'author_username' 이 개인정보 관련으로 보입니다.
- data/train/train-00000-of-00004.parquet: 컬럼명 'author_username' 이 개인정보 관련으로 보입니다.
- data/train/train-00000-of-00004.parquet: 컬럼명 'reviewer_username' 이 개인정보 관련으로 보입니다.
- data/train/train-00001-of-00003.parquet: 컬럼명 'reviewer_username' 이 개인정보 관련으로 보입니다.
- data/train/train-00001-of-00003.parquet: 컬럼명 'author_username' 이 개인정보 관련으로 보입니다.
- data/train/train-00001-of-00004.parquet: 컬럼명 'author_username' 이 개인정보 관련으로 보입니다.
- data/train/train-00001-of-00004.parquet: 컬럼명 'reviewer_username' 이 개인정보 관련으로 보입니다.
- data/train/train-00002-of-00003.parquet: 컬럼명 'reviewer_username' 이 개인정보 관련으로 보입니다.
- data/train/train-00002-of-00003.parquet: 컬럼명 'author_username' 이 개인정보 관련으로 보입니다.
- data/train/train-00002-of-00004.parquet: 컬럼명 'author_username' 이 개인정보 관련으로 보입니다.
- data/train/train-00002-of-00004.parquet: 컬럼명 'reviewer_username' 이 개인정보 관련으로 보입니다.
- data/train/train-00003-of-00004.parquet: 컬럼명 'author_username' 이 개인정보 관련으로 보입니다.
- data/train/train-00003-of-00004.parquet: 컬럼명 'reviewer_username' 이 개인정보 관련으로 보입니다.
- data/validation/validation-00000-of-00001.parquet: 컬럼명 'author_username' 이 개인정보 관련으로 보입니다.
- data/validation/validation-00000-of-00001.parquet: 컬럼명 'reviewer_username' 이 개인정보 관련으로 보입니다.

값 자체는 변형하지 않았습니다. 배포 전에 직접 확인하세요.

## 10. 축소 중 발견한 문제

- 원본 레코드 전체가 목표 용량 안에 들어가 전량을 포함했습니다.
- data/train/train-00000-of-00003.parquet: 중복 1,760건 제외
- data/train/train-00000-of-00004.parquet: 중복 30,296건 제외
- data/train/train-00001-of-00003.parquet: 중복 2,128건 제외
- data/train/train-00001-of-00004.parquet: 중복 26,983건 제외
- data/train/train-00002-of-00003.parquet: 중복 439건 제외
- data/train/train-00002-of-00004.parquet: 중복 31,193건 제외
- data/train/train-00003-of-00004.parquet: 중복 22,130건 제외
- data/validation/validation-00000-of-00001.parquet: 중복 3,001건 제외

## 11. 재현 방법

실제 실행한 명령:

```bash
python scripts/create_5gb_subset.py --input-path ./data/hf_source/github-codereview --output-path ./data/subset_5gb --target-size-gb 4.8 --max-size-gb 5.0 --seed 42 --overwrite
```

같은 원본과 같은 시드(`42`)를 쓰면 동일한 샘플이 선택됩니다.
선택된 레코드 목록은 `reports/subset_5gb_manifest.json` 에 있습니다.
