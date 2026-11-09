/* SPDX-License-Identifier: GPL-3.0-or-later */
#include <assert.h>
#include <float.h>
#include "libssc-matrix-private.h"

static void expect_identity(const float matrix[3][3])
{
    for (unsigned int row = 0; row < 3; row++)
        for (unsigned int col = 0; col < 3; col++)
            assert(matrix[row][col] == (row == col ? 1.0f : 0.0f));
}

static void reject(const float matrix[3][3], int complete)
{
    float output[3][3] = {{9, 8, 7}, {6, 5, 4}, {3, 2, 1}};
    assert(!ssc_matrix_accept(output, matrix, complete));
    expect_identity(output);
}

int main(void)
{
    float output[3][3];
    const float identity[3][3] = {{1, 0, 0}, {0, 1, 0}, {0, 0, 1}};
    const float signed_zero_sum[3][3] = {{1, 0, 0}, {0, -0.5f, -0.8660254f}, {0, 0.8660254f, -0.5f}};
    const float fractional[3][3] = {{0.70710678f, -0.70710678f, 0}, {0.70710678f, 0.70710678f, 0}, {0, 0, 1}};
    const float zero[3][3] = {{0}};
    const float fuxi[3][3] = {{0.1f, 0.1f, 0.1f}, {0, 0, 0}, {0, 0, 0}};
    const float shear[3][3] = {{1, 0.1f, 0.1f}, {0, 1, 0}, {0, 0, 1}};
    const float reflection[3][3] = {{-1, 0, 0}, {0, 1, 0}, {0, 0, 1}};
    const float repeated[3][3] = {{1, 0, 0}, {1, 0, 0}, {0, 0, 1}};
    const float scaled[3][3] = {{2, 0, 0}, {0, 2, 0}, {0, 0, 2}};
    float invalid[3][3];

    assert(ssc_matrix_accept(output, identity, 1));
    expect_identity(output);
    /* The old integer accumulation rejects this proper signed rotation. */
    assert(ssc_matrix_accept(output, signed_zero_sum, 1));
    assert(memcmp(output, signed_zero_sum, sizeof(output)) == 0);
    assert(ssc_matrix_accept(output, fractional, 1));
    assert(memcmp(output, fractional, sizeof(output)) == 0);
    reject(identity, 0);
    reject(zero, 1);
    reject(fuxi, 1);
    reject(shear, 1);
    reject(reflection, 1);
    reject(repeated, 1);
    reject(scaled, 1);
    for (unsigned int row = 0; row < 3; row++) {
        for (unsigned int col = 0; col < 3; col++) {
            const float bad[] = {NAN, INFINITY, -INFINITY, FLT_MAX};
            for (unsigned int i = 0; i < sizeof(bad) / sizeof(bad[0]); i++) {
                memcpy(invalid, identity, sizeof(invalid));
                invalid[row][col] = bad[i];
                reject(invalid, 1);
            }
        }
    }
    return 0;
}
