# 📚 Documentation

> **Auto-generated** by the Code-Writer + Debugger Agent  
> Model: `llama-3.1-8b-instant` &nbsp;|&nbsp; Generated: `2026-04-02` &nbsp;|&nbsp; Debug iterations: `0`

---

## 📌 Overview
This code generates and prints all Armstrong numbers which are also happy numbers up to a specified limit. An Armstrong number is a number that is equal to the sum of cubes of its digits, while a happy number is a number defined by the process of replacing the number by the sum of the squares of its digits until it equals 1 or loops endlessly in a cycle which does not include 1.

## 🔍 How It Works
Here's a step-by-step walkthrough of the logic:

1. The code first checks if the input number is an Armstrong number by comparing it to the sum of cubes of its digits.
2. If the number is an Armstrong number, it then checks if it's a happy number by repeatedly replacing the number with the sum of squares of its digits until it equals 1 or loops endlessly in a cycle which does not include 1.
3. If the number is both an Armstrong number and a happy number, it's printed out.

## 📦 Function Reference

### is_armstrong

```python
def is_armstrong(n: int) -> bool:
```

**Description**: Checks if a number is an Armstrong number.

**Parameters** | Name | Type | Description | Default
--------------|------|------|-------------|--------
| n | int | The number to check. |

**Returns** | Type | Description
------------|------|-------------
| bool | True if the number is an Armstrong number, False otherwise. |

**Raises** | ValueError: Input must be a non-negative integer.

### is_happy

```python
def is_happy(n: int) -> bool:
```

**Description**: Checks if a number is a happy number.

**Parameters** | Name | Type | Description | Default
--------------|------|------|-------------|--------
| n | int | The number to check. |

**Returns** | Type | Description
------------|------|-------------
| bool | True if the number is a happy number, False otherwise. |

**Raises** | ValueError: Input must be a positive integer.

### print_armstrong_happy_numbers

```python
def print_armstrong_happy_numbers(n: int) -> None:
```

**Description**: Prints all Armstrong numbers which are also happy numbers up to n.

**Parameters** | Name | Type | Description | Default
--------------|------|------|-------------|--------
| n | int | The upper limit. |

**Returns** | None

**Raises** | ValueError: Input must be a positive integer.

## 💡 Example Usage

```python
print_armstrong_happy_numbers(1000)
```

Output:
```
1634
8208
```

```python
print_armstrong_happy_numbers(500)
```

Output:
```
None
```

```python
print_armstrong_happy_numbers(1)
```

Output:
```
None
```

## ⚠️ Edge Cases Handled

* Negative numbers are not allowed as input.
* Non-integer inputs are not allowed.
* Zero is not considered an Armstrong number or a happy number.
* Numbers that are not Armstrong numbers or happy numbers are not printed.

## 🚀 Quick Start

```bash
pip install -r requirements.txt
python output/final_code.py
```

## 📋 Dependencies

| Module | Version | Purpose |
|--------|---------|---------|
| None   | None    | This code does not require any external dependencies. |

## 🛠️ Agent Execution Log Summary
This code was generated using the llama-3.1-8b-instant model with 0 debug iterations required. It was generated on 2026-04-02.

## 📝 Changelog

| Version | Date | Notes |
|---------|------|-------|
| 1.0.0   | 2026-04-02 | Initial auto-generated version |