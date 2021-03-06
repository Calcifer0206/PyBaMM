#
# Binary operator classes
#
import pybamm

import numpy as np
import numbers
from scipy.sparse import issparse, csr_matrix


class BinaryOperator(pybamm.Symbol):
    """A node in the expression tree representing a binary operator (e.g. `+`, `*`)

    Derived classes will specify the particular operator

    **Extends**: :class:`Symbol`

    Parameters
    ----------

    name : str
        name of the node
    left : :class:`Symbol` or :class:`Number`
        lhs child node (converted to :class:`Scalar` if Number)
    right : :class:`Symbol` or :class:`Number`
        rhs child node (converted to :class:`Scalar` if Number)

    """

    def __init__(self, name, left, right):
        left, right = self.format(left, right)

        domain = self.get_children_domains(left.domain, right.domain)
        auxiliary_domains = self.get_children_auxiliary_domains([left, right])
        super().__init__(
            name,
            children=[left, right],
            domain=domain,
            auxiliary_domains=auxiliary_domains,
        )
        self.left = self.children[0]
        self.right = self.children[1]

    def format(self, left, right):
        "Format children left and right into compatible form"
        # Turn numbers into scalars
        if isinstance(left, numbers.Number):
            left = pybamm.Scalar(left)
        if isinstance(right, numbers.Number):
            right = pybamm.Scalar(right)

        # Check both left and right are pybamm Symbols
        if not (isinstance(left, pybamm.Symbol) and isinstance(right, pybamm.Symbol)):
            raise NotImplementedError(
                """'{}' not implemented for symbols of type {} and {}""".format(
                    self.__class__.__name__, type(left), type(right)
                )
            )

        # Do some broadcasting in special cases, to avoid having to do this manually
        if left.domain != [] and right.domain != []:
            if (
                left.domain != right.domain
                and "secondary" in right.auxiliary_domains
                and left.domain == right.auxiliary_domains["secondary"]
            ):
                left = pybamm.PrimaryBroadcast(left, right.domain)
            if (
                right.domain != left.domain
                and "secondary" in left.auxiliary_domains
                and right.domain == left.auxiliary_domains["secondary"]
            ):
                right = pybamm.PrimaryBroadcast(right, left.domain)

        return left, right

    def __str__(self):
        """ See :meth:`pybamm.Symbol.__str__()`. """
        # Possibly add brackets for clarity
        if isinstance(self.left, pybamm.BinaryOperator) and not (
            (self.left.name == self.name)
            or (self.left.name == "*" and self.name == "/")
            or (self.left.name == "+" and self.name == "-")
            or self.name == "+"
        ):
            left_str = "({!s})".format(self.left)
        else:
            left_str = "{!s}".format(self.left)
        if isinstance(self.right, pybamm.BinaryOperator) and not (
            (self.name == "*" and self.right.name in ["*", "/"]) or self.name == "+"
        ):
            right_str = "({!s})".format(self.right)
        else:
            right_str = "{!s}".format(self.right)
        return "{} {} {}".format(left_str, self.name, right_str)

    def get_children_domains(self, ldomain, rdomain):
        "Combine domains from children in appropriate way"
        if ldomain == rdomain:
            return ldomain
        elif ldomain == []:
            return rdomain
        elif rdomain == []:
            return ldomain
        else:
            raise pybamm.DomainError(
                """
                children must have same (or empty) domains, but left.domain is '{}'
                and right.domain is '{}'
                """.format(
                    ldomain, rdomain
                )
            )

    def new_copy(self):
        """ See :meth:`pybamm.Symbol.new_copy()`. """

        # process children
        new_left = self.left.new_copy()
        new_right = self.right.new_copy()

        # make new symbol, ensure domain(s) remain the same
        out = self._binary_new_copy(new_left, new_right)
        out.copy_domains(self)

        return out

    def _binary_new_copy(self, left, right):
        "Default behaviour for new_copy"
        return self.__class__(left, right)

    def evaluate(self, t=None, y=None, y_dot=None, inputs=None, known_evals=None):
        """ See :meth:`pybamm.Symbol.evaluate()`. """
        if known_evals is not None:
            id = self.id
            try:
                return known_evals[id], known_evals
            except KeyError:
                left, known_evals = self.left.evaluate(t, y, y_dot, inputs, known_evals)
                right, known_evals = self.right.evaluate(
                    t, y, y_dot, inputs, known_evals
                )
                value = self._binary_evaluate(left, right)
                known_evals[id] = value
                return value, known_evals
        else:
            left = self.left.evaluate(t, y, y_dot, inputs)
            right = self.right.evaluate(t, y, y_dot, inputs)
            return self._binary_evaluate(left, right)

    def _evaluate_for_shape(self):
        """ See :meth:`pybamm.Symbol.evaluate_for_shape()`. """
        left = self.children[0].evaluate_for_shape()
        right = self.children[1].evaluate_for_shape()
        return self._binary_evaluate(left, right)

    def _binary_jac(self, left_jac, right_jac):
        """ Calculate the jacobian of a binary operator. """
        raise NotImplementedError

    def _binary_simplify(self, new_left, new_right):
        """ Simplify a binary operator. Default behaviour: unchanged"""
        return pybamm.simplify_if_constant(
            self._binary_new_copy(new_left, new_right), clear_domains=False
        )

    def _binary_evaluate(self, left, right):
        """ Perform binary operation on nodes 'left' and 'right'. """
        raise NotImplementedError

    def evaluates_on_edges(self, dimension):
        """ See :meth:`pybamm.Symbol.evaluates_on_edges()`. """
        return self.left.evaluates_on_edges(dimension) or self.right.evaluates_on_edges(
            dimension
        )

    def is_constant(self):
        """ See :meth:`pybamm.Symbol.is_constant()`. """
        return self.left.is_constant() and self.right.is_constant()


class Power(BinaryOperator):
    """A node in the expression tree representing a `**` power operator

    **Extends:** :class:`BinaryOperator`
    """

    def __init__(self, left, right):
        """ See :meth:`pybamm.BinaryOperator.__init__()`. """
        super().__init__("**", left, right)

    def _diff(self, variable):
        """ See :meth:`pybamm.Symbol._diff()`. """
        # apply chain rule and power rule
        base, exponent = self.orphans
        # derivative if variable is in the base
        diff = exponent * (base ** (exponent - 1)) * base.diff(variable)
        # derivative if variable is in the exponent (rare, check separately to avoid
        # unecessarily big tree)
        if any(variable.id == x.id for x in exponent.pre_order()):
            diff += (base ** exponent) * pybamm.log(base) * exponent.diff(variable)
        return diff

    def _binary_jac(self, left_jac, right_jac):
        """ See :meth:`pybamm.BinaryOperator._binary_jac()`. """
        # apply chain rule and power rule
        left, right = self.orphans
        if left.evaluates_to_constant_number() and right.evaluates_to_constant_number():
            return pybamm.Scalar(0)
        elif right.evaluates_to_constant_number():
            return (right * left ** (right - 1)) * left_jac
        elif left.evaluates_to_constant_number():
            return (left ** right * pybamm.log(left)) * right_jac
        else:
            return (left ** (right - 1)) * (
                right * left_jac + left * pybamm.log(left) * right_jac
            )

    def _binary_evaluate(self, left, right):
        """ See :meth:`pybamm.BinaryOperator._binary_evaluate()`. """
        # don't raise RuntimeWarning for NaNs
        with np.errstate(invalid="ignore"):
            return left ** right


class Addition(BinaryOperator):
    """A node in the expression tree representing an addition operator

    **Extends:** :class:`BinaryOperator`
    """

    def __init__(self, left, right):
        """ See :meth:`pybamm.BinaryOperator.__init__()`. """
        super().__init__("+", left, right)

    def _diff(self, variable):
        """ See :meth:`pybamm.Symbol._diff()`. """
        return self.left.diff(variable) + self.right.diff(variable)

    def _binary_jac(self, left_jac, right_jac):
        """ See :meth:`pybamm.BinaryOperator._binary_jac()`. """
        return left_jac + right_jac

    def _binary_evaluate(self, left, right):
        """ See :meth:`pybamm.BinaryOperator._binary_evaluate()`. """
        return left + right

    def _binary_simplify(self, left, right):
        """
        See :meth:`pybamm.BinaryOperator._binary_simplify()`.
        """
        return pybamm.simplify_addition_subtraction(self.__class__, left, right)


class Subtraction(BinaryOperator):
    """A node in the expression tree representing a subtraction operator

    **Extends:** :class:`BinaryOperator`
    """

    def __init__(self, left, right):
        """ See :meth:`pybamm.BinaryOperator.__init__()`. """

        super().__init__("-", left, right)

    def _diff(self, variable):
        """ See :meth:`pybamm.Symbol._diff()`. """
        return self.left.diff(variable) - self.right.diff(variable)

    def _binary_jac(self, left_jac, right_jac):
        """ See :meth:`pybamm.BinaryOperator._binary_jac()`. """
        return left_jac - right_jac

    def _binary_evaluate(self, left, right):
        """ See :meth:`pybamm.BinaryOperator._binary_evaluate()`. """
        return left - right

    def _binary_simplify(self, left, right):
        """
        See :meth:`pybamm.BinaryOperator._binary_simplify()`.
        """
        return pybamm.simplify_addition_subtraction(self.__class__, left, right)


class Multiplication(BinaryOperator):
    """
    A node in the expression tree representing a multiplication operator
    (Hadamard product). Overloads cases where the "*" operator would usually return a
    matrix multiplication (e.g. scipy.sparse.coo.coo_matrix)

    **Extends:** :class:`BinaryOperator`
    """

    def __init__(self, left, right):
        """ See :meth:`pybamm.BinaryOperator.__init__()`. """

        super().__init__("*", left, right)

    def _diff(self, variable):
        """ See :meth:`pybamm.Symbol._diff()`. """
        # apply product rule
        left, right = self.orphans
        return left.diff(variable) * right + left * right.diff(variable)

    def _binary_jac(self, left_jac, right_jac):
        """ See :meth:`pybamm.BinaryOperator._binary_jac()`. """
        # apply product rule
        left, right = self.orphans
        if left.evaluates_to_constant_number() and right.evaluates_to_constant_number():
            return pybamm.Scalar(0)
        elif left.evaluates_to_constant_number():
            return left * right_jac
        elif right.evaluates_to_constant_number():
            return right * left_jac
        else:
            return right * left_jac + left * right_jac

    def _binary_evaluate(self, left, right):
        """ See :meth:`pybamm.BinaryOperator._binary_evaluate()`. """

        if issparse(left):
            return csr_matrix(left.multiply(right))
        elif issparse(right):
            # Hadamard product is commutative, so we can switch right and left
            return csr_matrix(right.multiply(left))
        else:
            return left * right

    def _binary_simplify(self, left, right):
        """ See :meth:`pybamm.BinaryOperator._binary_simplify()`. """
        return pybamm.simplify_multiplication_division(self.__class__, left, right)


class MatrixMultiplication(BinaryOperator):
    """A node in the expression tree representing a matrix multiplication operator

    **Extends:** :class:`BinaryOperator`
    """

    def __init__(self, left, right):
        """ See :meth:`pybamm.BinaryOperator.__init__()`. """

        super().__init__("@", left, right)

    def diff(self, variable):
        """ See :meth:`pybamm.Symbol.diff()`. """
        # We shouldn't need this
        raise NotImplementedError(
            "diff not implemented for symbol of type 'MatrixMultiplication'"
        )

    def _binary_jac(self, left_jac, right_jac):
        """ See :meth:`pybamm.BinaryOperator._binary_jac()`. """
        # We only need the case where left is an array and right
        # is a (slice of a) state vector, e.g. for discretised spatial
        # operators of the form D @ u (also catch cases of (-D) @ u)
        left, right = self.orphans
        if isinstance(left, pybamm.Array) or (
            isinstance(left, pybamm.Negate) and isinstance(left.child, pybamm.Array)
        ):
            left = pybamm.Matrix(csr_matrix(left.evaluate()))
            return left @ right_jac
        else:
            raise NotImplementedError(
                """jac of 'MatrixMultiplication' is only
             implemented for left of type 'pybamm.Array',
             not {}""".format(
                    left.__class__
                )
            )

    def _binary_evaluate(self, left, right):
        """ See :meth:`pybamm.BinaryOperator._binary_evaluate()`. """
        return left @ right

    def _binary_simplify(self, left, right):
        """ See :meth:`pybamm.BinaryOperator._binary_simplify()`. """
        return pybamm.simplify_multiplication_division(self.__class__, left, right)


class Division(BinaryOperator):
    """A node in the expression tree representing a division operator

    **Extends:** :class:`BinaryOperator`
    """

    def __init__(self, left, right):
        """ See :meth:`pybamm.BinaryOperator.__init__()`. """
        super().__init__("/", left, right)

    def _diff(self, variable):
        """ See :meth:`pybamm.Symbol._diff()`. """
        # apply quotient rule
        top, bottom = self.orphans
        return (top.diff(variable) * bottom - top * bottom.diff(variable)) / bottom ** 2

    def _binary_jac(self, left_jac, right_jac):
        """ See :meth:`pybamm.BinaryOperator._binary_jac()`. """
        # apply quotient rule
        left, right = self.orphans
        if left.evaluates_to_constant_number() and right.evaluates_to_constant_number():
            return pybamm.Scalar(0)
        elif left.evaluates_to_constant_number():
            return -left / right ** 2 * right_jac
        elif right.evaluates_to_constant_number():
            return left_jac / right
        else:
            return (right * left_jac - left * right_jac) / right ** 2

    def _binary_evaluate(self, left, right):
        """ See :meth:`pybamm.BinaryOperator._binary_evaluate()`. """

        if issparse(left):
            return csr_matrix(left.multiply(1 / right))
        else:
            if isinstance(right, numbers.Number) and right == 0:
                # don't raise RuntimeWarning for NaNs
                with np.errstate(invalid="ignore"):
                    return left * np.inf
            else:
                return left / right

    def _binary_simplify(self, left, right):
        """ See :meth:`pybamm.BinaryOperator._binary_simplify()`. """
        return pybamm.simplify_multiplication_division(self.__class__, left, right)


class Inner(BinaryOperator):
    """
    A node in the expression tree which represents the inner (or dot) product. This
    operator should be used to take the inner product of two mathematical vectors
    (as opposed to the computational vectors arrived at post-discretisation) of the
    form v = v_x e_x + v_y e_y + v_z e_z where v_x, v_y, v_z are scalars
    and e_x, e_y, e_z are x-y-z-directional unit vectors. For v and w mathematical
    vectors, inner product returns v_x * w_x + v_y * w_y + v_z * w_z. In addition,
    for some spatial discretisations mathematical vector quantities (such as
    i = grad(phi) ) are evaluated on a different part of the grid to mathematical
    scalars (e.g. for finite volume mathematical scalars are evaluated on the nodes but
    mathematical vectors are evaluated on cell edges). Therefore, inner also transfers
    the inner product of the vector onto the scalar part of the grid if required
    by a particular discretisation.

    **Extends:** :class:`BinaryOperator`
    """

    def __init__(self, left, right):
        """ See :meth:`pybamm.BinaryOperator.__init__()`. """
        super().__init__("inner product", left, right)

    def _diff(self, variable):
        """ See :meth:`pybamm.Symbol._diff()`. """
        # apply product rule
        left, right = self.orphans
        return left.diff(variable) * right + left * right.diff(variable)

    def _binary_jac(self, left_jac, right_jac):
        """ See :meth:`pybamm.BinaryOperator._binary_jac()`. """
        # apply product rule
        left, right = self.orphans
        if left.evaluates_to_constant_number() and right.evaluates_to_constant_number():
            return pybamm.Scalar(0)
        elif left.evaluates_to_constant_number():
            return left * right_jac
        elif right.evaluates_to_constant_number():
            return right * left_jac
        else:
            return right * left_jac + left * right_jac

    def _binary_evaluate(self, left, right):
        """ See :meth:`pybamm.BinaryOperator._binary_evaluate()`. """

        if issparse(left):
            return left.multiply(right)
        elif issparse(right):
            # Hadamard product is commutative, so we can switch right and left
            return right.multiply(left)
        else:
            return left * right

    def _binary_simplify(self, left, right):
        """ See :meth:`pybamm.BinaryOperator._binary_simplify()`. """
        return pybamm.simplify_multiplication_division(self.__class__, left, right)

    def evaluates_on_edges(self, dimension):
        """ See :meth:`pybamm.Symbol.evaluates_on_edges()`. """
        return False


def inner(left, right):
    """
    Return inner product of two symbols.
    """
    left, right = pybamm.preprocess(left, right)
    # simplify multiply by scalar zero, being careful about shape
    if pybamm.is_scalar_zero(left):
        return pybamm.zeros_like(right)
    if pybamm.is_scalar_zero(right):
        return pybamm.zeros_like(left)

    # if one of the children is a zero matrix, we have to be careful about shapes
    if pybamm.is_matrix_zero(left) or pybamm.is_matrix_zero(right):
        return pybamm.zeros_like(pybamm.Inner(left, right))

    # anything multiplied by a scalar one returns itself
    if pybamm.is_scalar_one(left):
        return right
    if pybamm.is_scalar_one(right):
        return left

    return pybamm.simplify_if_constant(pybamm.Inner(left, right), clear_domains=False)


class Heaviside(BinaryOperator):
    """A node in the expression tree representing a heaviside step function.

    Adding this operation to the rhs or algebraic equations in a model can often cause a
    discontinuity in the solution. For the specific cases listed below, this will be
    automatically handled by the solver. In the general case, you can explicitly tell
    the solver of discontinuities by adding a :class:`Event` object with
    :class:`EventType` DISCONTINUITY to the model's list of events.

    In the case where the Heaviside function is of the form `pybamm.t < x`, `pybamm.t <=
    x`, `x < pybamm.t`, or `x <= pybamm.t`, where `x` is any constant equation, this
    DISCONTINUITY event will automatically be added by the solver.

    **Extends:** :class:`BinaryOperator`
    """

    def __init__(self, name, left, right):
        """ See :meth:`pybamm.BinaryOperator.__init__()`. """
        super().__init__(name, left, right)

    def diff(self, variable):
        """ See :meth:`pybamm.Symbol.diff()`. """
        # Heaviside should always be multiplied by something else so hopefully don't
        # need to worry about shape
        return pybamm.Scalar(0)

    def _binary_jac(self, left_jac, right_jac):
        """ See :meth:`pybamm.BinaryOperator._binary_jac()`. """
        # Heaviside should always be multiplied by something else so hopefully don't
        # need to worry about shape
        return pybamm.Scalar(0)


class EqualHeaviside(Heaviside):
    "A heaviside function with equality (return 1 when left = right)"

    def __init__(self, left, right):
        """ See :meth:`pybamm.BinaryOperator.__init__()`. """
        super().__init__("<=", left, right)

    def __str__(self):
        """ See :meth:`pybamm.Symbol.__str__()`. """
        return "{!s} <= {!s}".format(self.left, self.right)

    def _binary_evaluate(self, left, right):
        """ See :meth:`pybamm.BinaryOperator._binary_evaluate()`. """
        # don't raise RuntimeWarning for NaNs
        with np.errstate(invalid="ignore"):
            return left <= right


class NotEqualHeaviside(Heaviside):
    "A heaviside function without equality (return 0 when left = right)"

    def __init__(self, left, right):
        super().__init__("<", left, right)

    def __str__(self):
        """ See :meth:`pybamm.Symbol.__str__()`. """
        return "{!s} < {!s}".format(self.left, self.right)

    def _binary_evaluate(self, left, right):
        """ See :meth:`pybamm.BinaryOperator._binary_evaluate()`. """
        # don't raise RuntimeWarning for NaNs
        with np.errstate(invalid="ignore"):
            return left < right


class Modulo(BinaryOperator):
    "Calculates the remainder of an integer division"

    def __init__(self, left, right):
        super().__init__("%", left, right)

    def _diff(self, variable):
        """ See :meth:`pybamm.Symbol._diff()`. """
        # apply chain rule and power rule
        left, right = self.orphans
        # derivative if variable is in the base
        diff = left.diff(variable)
        # derivative if variable is in the right term (rare, check separately to avoid
        # unecessarily big tree)
        if any(variable.id == x.id for x in right.pre_order()):
            diff += -pybamm.Floor(left / right) * right.diff(variable)
        return diff

    def _binary_jac(self, left_jac, right_jac):
        """ See :meth:`pybamm.BinaryOperator._binary_jac()`. """
        # apply chain rule and power rule
        left, right = self.orphans
        if left.evaluates_to_constant_number() and right.evaluates_to_constant_number():
            return pybamm.Scalar(0)
        elif right.evaluates_to_constant_number():
            return left_jac
        elif left.evaluates_to_constant_number():
            return -right_jac * pybamm.Floor(left / right)
        else:
            return left_jac - right_jac * pybamm.Floor(left / right)

    def __str__(self):
        """ See :meth:`pybamm.Symbol.__str__()`. """
        return "{!s} mod {!s}".format(self.left, self.right)

    def _binary_evaluate(self, left, right):
        """ See :meth:`pybamm.BinaryOperator._binary_evaluate()`. """
        return left % right


class Minimum(BinaryOperator):
    " Returns the smaller of two objects "

    def __init__(self, left, right):
        super().__init__("minimum", left, right)

    def __str__(self):
        """ See :meth:`pybamm.Symbol.__str__()`. """
        return "minimum({!s}, {!s})".format(self.left, self.right)

    def _diff(self, variable):
        """ See :meth:`pybamm.Symbol._diff()`. """
        left, right = self.orphans
        return (left <= right) * left.diff(variable) + (left > right) * right.diff(
            variable
        )

    def _binary_jac(self, left_jac, right_jac):
        """ See :meth:`pybamm.BinaryOperator._binary_jac()`. """
        left, right = self.orphans
        return (left <= right) * left_jac + (left > right) * right_jac

    def _binary_evaluate(self, left, right):
        """ See :meth:`pybamm.BinaryOperator._binary_evaluate()`. """
        # don't raise RuntimeWarning for NaNs
        return np.minimum(left, right)


class Maximum(BinaryOperator):
    " Returns the smaller of two objects "

    def __init__(self, left, right):
        super().__init__("maximum", left, right)

    def __str__(self):
        """ See :meth:`pybamm.Symbol.__str__()`. """
        return "maximum({!s}, {!s})".format(self.left, self.right)

    def _diff(self, variable):
        """ See :meth:`pybamm.Symbol._diff()`. """
        left, right = self.orphans
        return (left >= right) * left.diff(variable) + (left < right) * right.diff(
            variable
        )

    def _binary_jac(self, left_jac, right_jac):
        """ See :meth:`pybamm.BinaryOperator._binary_jac()`. """
        left, right = self.orphans
        return (left >= right) * left_jac + (left < right) * right_jac

    def _binary_evaluate(self, left, right):
        """ See :meth:`pybamm.BinaryOperator._binary_evaluate()`. """
        # don't raise RuntimeWarning for NaNs
        return np.maximum(left, right)


def minimum(left, right):
    """
    Returns the smaller of two objects, possibly with a smoothing approximation.
    Not to be confused with :meth:`pybamm.min`, which returns min function of child.
    """
    k = pybamm.settings.min_smoothing
    # Return exact approximation if that is the setting or the outcome is a constant
    # (i.e. no need for smoothing)
    if k == "exact" or (pybamm.is_constant(left) and pybamm.is_constant(right)):
        out = Minimum(left, right)
    else:
        out = pybamm.softminus(left, right, k)
    return pybamm.simplify_if_constant(out, clear_domains=False)


def maximum(left, right):
    """
    Returns the larger of two objects, possibly with a smoothing approximation.
    Not to be confused with :meth:`pybamm.max`, which returns max function of child.
    """
    k = pybamm.settings.max_smoothing
    # Return exact approximation if that is the setting or the outcome is a constant
    # (i.e. no need for smoothing)
    if k == "exact" or (pybamm.is_constant(left) and pybamm.is_constant(right)):
        out = Maximum(left, right)
    else:
        out = pybamm.softplus(left, right, k)
    return pybamm.simplify_if_constant(out, clear_domains=False)


def softminus(left, right, k):
    """
    Softplus approximation to the minimum function. k is the smoothing parameter,
    set by `pybamm.settings.min_smoothing`. The recommended value is k=10.
    """
    return pybamm.log(pybamm.exp(-k * left) + pybamm.exp(-k * right)) / -k


def softplus(left, right, k):
    """
    Softplus approximation to the maximum function. k is the smoothing parameter,
    set by `pybamm.settings.max_smoothing`. The recommended value is k=10.
    """
    return pybamm.log(pybamm.exp(k * left) + pybamm.exp(k * right)) / k


def sigmoid(left, right, k):
    """
    Sigmoidal approximation to the heaviside function. k is the smoothing parameter,
    set by `pybamm.settings.heaviside_smoothing`. The recommended value is k=10.
    Note that the concept of deciding which side to pick when left=right does not apply
    for this smooth approximation. When left=right, the value is (left+right)/2.
    """
    return (1 + pybamm.tanh(k * (right - left))) / 2


def source(left, right, boundary=False):
    """A convenience function for creating (part of) an expression tree representing
    a source term. This is necessary for spatial methods where the mass matrix
    is not the identity (e.g. finite element formulation with piecwise linear
    basis functions). The left child is the symbol representing the source term
    and the right child is the symbol of the equation variable (currently, the
    finite element formulation in PyBaMM assumes all functions are constructed
    using the same basis, and the matrix here is constructed accoutning for the
    boundary conditions of the right child). The method returns the matrix-vector
    product of the mass matrix (adjusted to account for any Dirichlet boundary
    conditions imposed the the right symbol) and the discretised left symbol.

    Parameters
    ----------

    left : :class:`Symbol`
        The left child node, which represents the expression for the source term.
    right : :class:`Symbol`
        The right child node. This is the symbol whose boundary conditions are
        accounted for in the construction of the mass matrix.
    boundary : bool, optional
        If True, then the mass matrix should is assembled over the boundary,
        corresponding to a source term which only acts on the boundary of the
        domain. If False (default), the matrix is assembled over the entire domain,
        corresponding to a source term in the bulk.

    """
    # Broadcast if left is number
    if isinstance(left, numbers.Number):
        left = pybamm.PrimaryBroadcast(left, "current collector")

    if left.domain != ["current collector"] or right.domain != ["current collector"]:
        raise pybamm.DomainError(
            """'source' only implemented in the 'current collector' domain,
            but symbols have domains {} and {}""".format(
                left.domain, right.domain
            )
        )
    if boundary:
        return pybamm.BoundaryMass(right) @ left
    else:
        return pybamm.Mass(right) @ left
