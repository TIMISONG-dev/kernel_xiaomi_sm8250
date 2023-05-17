/* SPDX-License-Identifier: GPL-2.0 */
#ifndef _LIB_UBSAN_H
#define _LIB_UBSAN_H

enum {
	type_kind_int = 0,
	type_kind_float = 1,
	type_unknown = 0xffff
};

struct type_descriptor {
	u16 type_kind;
	u16 type_info;
	char type_name[1];
};

struct source_location {
	const char *file_name;
	union {
		unsigned long reported;
		struct {
			u32 line;
			u32 column;
		};
	};
};

struct overflow_data {
	struct source_location location;
	struct type_descriptor *type;
};

struct type_mismatch_data {
	struct source_location location;
	struct type_descriptor *type;
	unsigned long alignment;
	unsigned char type_check_kind;
};

struct type_mismatch_data_v1 {
	struct source_location location;
	struct type_descriptor *type;
	unsigned char log_alignment;
	unsigned char type_check_kind;
};

struct type_mismatch_data_common {
	struct source_location *location;
	struct type_descriptor *type;
	unsigned long alignment;
	unsigned char type_check_kind;
};

struct nonnull_arg_data {
	struct source_location location;
	struct source_location attr_location;
	int arg_index;
};

struct vla_bound_data {
	struct source_location location;
	struct type_descriptor *type;
};

struct out_of_bounds_data {
	struct source_location location;
	struct type_descriptor *array_type;
	struct type_descriptor *index_type;
};

struct shift_out_of_bounds_data {
	struct source_location location;
	struct type_descriptor *lhs_type;
	struct type_descriptor *rhs_type;
};

struct unreachable_data {
	struct source_location location;
};

struct invalid_value_data {
	struct source_location location;
	struct type_descriptor *type;
};

#if defined(CONFIG_ARCH_SUPPORTS_INT128) && defined(__SIZEOF_INT128__)
typedef __int128 s_max;
typedef unsigned __int128 u_max;
#else
typedef s64 s_max;
typedef u64 u_max;
#endif

/* Compiler-generated calls into the UBSAN runtime. */
void __ubsan_handle_add_overflow(struct overflow_data *data,
				void *lhs, void *rhs);
void __ubsan_handle_sub_overflow(struct overflow_data *data,
				void *lhs, void *rhs);
void __ubsan_handle_mul_overflow(struct overflow_data *data,
				void *lhs, void *rhs);
void __ubsan_handle_negate_overflow(struct overflow_data *data,
				void *old_val);
void __ubsan_handle_divrem_overflow(struct overflow_data *data,
				void *lhs, void *rhs);
void __ubsan_handle_type_mismatch(struct type_mismatch_data *data,
				void *ptr);
void __ubsan_handle_type_mismatch_v1(struct type_mismatch_data_v1 *data,
				void *ptr);
void __ubsan_handle_vla_bound_not_positive(struct vla_bound_data *data,
					void *bound);
void __ubsan_handle_out_of_bounds(struct out_of_bounds_data *data, void *index);
void __ubsan_handle_shift_out_of_bounds(struct shift_out_of_bounds_data *data,
					void *lhs, void *rhs);
void __ubsan_handle_builtin_unreachable(struct unreachable_data *data);
void __ubsan_handle_load_invalid_value(struct invalid_value_data *data,
				void *val);

#endif
